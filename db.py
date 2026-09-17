"""
Thin SQLite helper layer. One connection per call (SQLite + a small
single-box cron/Flask app doesn't need pooling); WAL mode lets the hourly
scraper write while the dashboard reads without locking each other out.
"""
import json
import statistics
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import config


# Retired six-category scheme -> the current three. Used by _migrate() to
# bring an existing droplet's curated lists onto the new categories; see
# scraper/subreddits.py's CATEGORIES for the live set.
LEGACY_CATEGORY_MAP = {
    "animals": "funny-viral",
    "gaming": "funny-viral",
    "wins": "funny-viral",
    "oddly-satisfying": "funny-viral",
    "mildly-infuriating": "funny-viral",
}


def _connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # The module docstring above has always claimed WAL mode is on, but this
    # pragma was never actually set -- the DB has been running in SQLite's
    # default rollback-journal mode the whole time. In that mode a writer
    # (the hourly scraper inserting new clips) locks the *entire* database
    # file for the duration of its transaction, blocking any other writer
    # (e.g. a triage keep/reject DELETE) until the lock clears or the
    # connect-time timeout (30s) is hit. That's why clicking Delete in
    # /triage can appear to freeze the page for up to 30s and then land on
    # a 500 (sqlite3.OperationalError: database is locked) if it loses the
    # race. WAL mode lets readers and a single writer proceed concurrently
    # instead of blocking each other.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def get_conn():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    schema_path = config.BASE_DIR / "db" / "schema.sql"
    with get_conn() as conn:
        conn.executescript(schema_path.read_text())
    _migrate()
    _migrate_scrape_runs_fk()


def _migrate():
    """
    Defensive ALTER TABLEs for columns added after a DB may already have
    been created (CREATE TABLE IF NOT EXISTS in schema.sql doesn't touch
    an existing table). Safe to call every startup.
    """
    with get_conn() as conn:
        # One-time migration: this table used to hold YouTube search
        # hashtags, back when YouTube (not TikTok) was the keyword/hashtag-
        # driven source. schema.sql (run just above, before this function)
        # already created an empty tiktok_hashtags table for fresh installs
        # -- on an existing droplet DB the old youtube_hashtags table is
        # still sitting there too, so merge its already-curated hashtags
        # into the new table instead of silently starting from empty, then
        # drop the old one.
        tables = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
        if "youtube_hashtags" in tables:
            conn.execute(
                "INSERT OR IGNORE INTO tiktok_hashtags (hashtag, category, added_at) "
                "SELECT hashtag, category, added_at FROM youtube_hashtags"
            )
            conn.execute("DROP TABLE youtube_hashtags")

        existing = {row["name"] for row in conn.execute("PRAGMA table_info(clips)")}
        if "media_type" not in existing:
            conn.execute("ALTER TABLE clips ADD COLUMN media_type TEXT NOT NULL DEFAULT 'video'")
        if "triaged" not in existing:
            conn.execute("ALTER TABLE clips ADD COLUMN triaged INTEGER NOT NULL DEFAULT 0")
        for col, ddl in (
            ("likes", "ALTER TABLE clips ADD COLUMN likes REAL"),
            ("comments", "ALTER TABLE clips ADD COLUMN comments REAL"),
            ("shares", "ALTER TABLE clips ADD COLUMN shares REAL"),
            ("engagement_score", "ALTER TABLE clips ADD COLUMN engagement_score REAL"),
            ("tags", "ALTER TABLE clips ADD COLUMN tags TEXT"),
            ("transcript", "ALTER TABLE clips ADD COLUMN transcript TEXT"),
        ):
            if col not in existing:
                conn.execute(ddl)

        # One-time migration: the old per-platform "Subreddits" and "TikTok
        # Hashtags" tabs are now the single unified "Search Parameters" list
        # (search_terms table) -- fold the TikTok hashtags in as search
        # terms so nothing curated gets lost. Subreddits are NOT folded in
        # here -- Reddit sourcing switched from subreddit-based to
        # keyword-based, so the `subreddits` table now means something
        # different (an adaptive origin dimension, see subreddit_votes)
        # rather than "a search term".
        if not conn.execute("SELECT 1 FROM search_terms LIMIT 1").fetchone():
            conn.execute(
                "INSERT OR IGNORE INTO search_terms (term, category, added_at) "
                "SELECT hashtag, category, added_at FROM tiktok_hashtags"
            )

        # The six-category scheme (animals/gaming/wins/oddly-satisfying/
        # mildly-infuriating) was retired in favour of three
        # (funny-viral/fails/political-satire). Rows carried over from an
        # existing droplet -- both the hashtags just migrated above and the
        # curated `subreddits` list -- still hold the retired names, and a
        # retired category on a search term propagates onto every clip that
        # term goes on to pull. Remap them in place. Historical `clips` rows
        # are deliberately left alone (that's a record of what actually
        # happened; the dashboard still groups them fine).
        for old, new in LEGACY_CATEGORY_MAP.items():
            conn.execute("UPDATE search_terms SET category = ? WHERE category = ?", (new, old))
            conn.execute("UPDATE subreddits SET category = ? WHERE category = ?", (new, old))

        # One-time backfill: seed triage_stats' "keeps" side from clips that
        # were already triaged=1 before this feature existed. Rejected
        # historical items were hard-deleted by /triage at the time (no undo,
        # see README) and are not recoverable -- only kept items can be
        # backfilled; rejects start counting from whenever this migration
        # first runs.
        already_backfilled = conn.execute(
            "SELECT 1 FROM settings WHERE key = 'triage_stats_backfilled'"
        ).fetchone()
        if not already_backfilled:
            rows = conn.execute(
                "SELECT source, subreddit, COUNT(*) AS n FROM clips WHERE triaged = 1 "
                "GROUP BY source, subreddit"
            ).fetchall()
            for row in rows:
                subgroup = row["subreddit"] if row["source"] == "reddit" and row["subreddit"] else "all"
                conn.execute(
                    "INSERT INTO triage_stats (source, subgroup, keeps, rejects) VALUES (?, ?, ?, 0) "
                    "ON CONFLICT(source, subgroup) DO UPDATE SET keeps = keeps + excluded.keeps",
                    (row["source"], subgroup, row["n"]),
                )
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES ('triage_stats_backfilled', '1', ?) "
                "ON CONFLICT(key) DO NOTHING",
                (utcnow_iso(),),
            )


def utcnow_iso():
    return datetime.now(timezone.utc).isoformat()


# --- dedup ---------------------------------------------------------------

def is_duplicate(source: str, source_id: str, window_hours: int = None) -> bool:
    window_hours = window_hours or config.DEDUP_WINDOW_HOURS
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM clips WHERE source = ? AND source_id = ? AND fetched_at > ? LIMIT 1",
            (source, source_id, cutoff),
        ).fetchone()
    return row is not None


# --- clips -----------------------------------------------------------------

def insert_clip(**fields) -> int:
    fields.setdefault("fetched_at", utcnow_iso())
    fields.setdefault("status", "new")
    fields.setdefault("media_type", "video")
    fields.setdefault("triaged", 0)
    cols = ", ".join(fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    with get_conn() as conn:
        cur = conn.execute(
            f"INSERT INTO clips ({cols}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        return cur.lastrowid


def get_clips_for_date(date_str: str, triaged_only: bool = True):
    """
    date_str: 'YYYY-MM-DD' (matches the /media/<date>/ folder convention).
    triaged_only=True (the /review default) only returns items that have
    been through the /triage accept/reject pass -- untriaged items live in
    /triage until you act on them.
    """
    query = "SELECT * FROM clips WHERE fetched_at LIKE ?"
    params = [f"{date_str}%"]
    if triaged_only:
        query += " AND triaged = 1"
    query += " ORDER BY fetched_at DESC"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# --- triage (accept/reject first pass) --------------------------------

def get_next_untriaged_clip(date_str: str | None = None):
    """Oldest not-yet-triaged item first, optionally restricted to one date."""
    query = "SELECT * FROM clips WHERE triaged = 0"
    params: list = []
    if date_str:
        query += " AND fetched_at LIKE ?"
        params.append(f"{date_str}%")
    query += " ORDER BY fetched_at ASC LIMIT 1"
    with get_conn() as conn:
        row = conn.execute(query, params).fetchone()
    return dict(row) if row else None


def count_untriaged(date_str: str | None = None) -> int:
    query = "SELECT COUNT(*) AS n FROM clips WHERE triaged = 0"
    params: list = []
    if date_str:
        query += " AND fetched_at LIKE ?"
        params.append(f"{date_str}%")
    with get_conn() as conn:
        row = conn.execute(query, params).fetchone()
    return row["n"]


def set_triaged(clip_id: int, triaged: bool = True):
    with get_conn() as conn:
        conn.execute("UPDATE clips SET triaged = ? WHERE id = ?", (1 if triaged else 0, clip_id))


def get_clip(clip_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    return dict(row) if row else None


def delete_clip(clip_id: int):
    """
    Deletes the DB row only and returns it (so the caller -- app.py's
    /triage reject route -- can remove the underlying file(s) from disk).
    """
    clip = get_clip(clip_id)
    if clip is None:
        return None
    with get_conn() as conn:
        conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
    return clip


def get_available_dates():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT substr(fetched_at, 1, 10) AS d FROM clips ORDER BY d DESC"
        ).fetchall()
    return [r["d"] for r in rows]


def get_clips_by_ids(ids):
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM clips WHERE id IN ({placeholders})", tuple(ids)
        ).fetchall()
    by_id = {r["id"]: dict(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]  # preserve caller's order


def mark_clips_status(ids, status):
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE clips SET status = ? WHERE id IN ({placeholders})",
            (status, *ids),
        )


def set_clips_compilation(ids, compilation_id):
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE clips SET status = 'used', compilation_id = ? WHERE id IN ({placeholders})",
            (compilation_id, *ids),
        )


# --- scrape_runs -----------------------------------------------------------

def log_scrape_run(candidates_seen: int, clip_id: int | None, note: str = ""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO scrape_runs (run_at, candidates_seen, clip_id, note) VALUES (?, ?, ?, ?)",
            (utcnow_iso(), candidates_seen, clip_id, note),
        )


# --- compilations ------------------------------------------------------

def create_compilation(clip_ids) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO compilations (created_at, clip_ids, status) VALUES (?, ?, 'building')",
            (utcnow_iso(), json.dumps(clip_ids)),
        )
        return cur.lastrowid


def update_compilation(compilation_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE compilations SET {cols} WHERE id = ?",
            (*fields.values(), compilation_id),
        )


def get_compilation(compilation_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM compilations WHERE id = ?", (compilation_id,)
        ).fetchone()
    return dict(row) if row else None


def list_compilations(limit=25):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM compilations ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# --- jobs (background build/upload progress) --------------------------

def create_job(kind: str, ref_id=None) -> int:
    now = utcnow_iso()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO jobs (kind, ref_id, status, created_at, updated_at) VALUES (?, ?, 'pending', ?, ?)",
            (kind, ref_id, now, now),
        )
        return cur.lastrowid


def update_job(job_id, **fields):
    fields["updated_at"] = utcnow_iso()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))


def get_job(job_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


# --- settings (dashboard-editable API credentials, see api_settings.py) ---

def get_all_settings() -> dict:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, utcnow_iso()),
        )


def delete_setting(key: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def get_setting_value(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


# --- subreddits (the live, dashboard-editable scrape source list) ------

def has_any_subreddits() -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM subreddits LIMIT 1").fetchone()
    return row is not None


def bulk_seed_subreddits(mapping: dict) -> None:
    """INSERT OR IGNORE every (name, category) pair -- used once, when the
    table is empty, to seed it from scraper.subreddits' default list."""
    now = utcnow_iso()
    with get_conn() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO subreddits (name, category, added_at) VALUES (?, ?, ?)",
            [(name, category, now) for name, category in mapping.items()],
        )


def list_subreddit_names(category: str | None = None) -> list[str]:
    query = "SELECT name FROM subreddits"
    params: tuple = ()
    if category:
        query += " WHERE category = ?"
        params = (category,)
    query += " ORDER BY name"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [r["name"] for r in rows]


def list_subreddits_full() -> list[dict]:
    """[{name, category, added_at}, ...], for the /subreddits dashboard page."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM subreddits ORDER BY category, name COLLATE NOCASE").fetchall()
    return [dict(r) for r in rows]


def get_subreddit_category(name: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT category FROM subreddits WHERE name = ?", (name,)).fetchone()
    return row["category"] if row else None


def find_subreddit_case_insensitive(name: str) -> str | None:
    """Returns the stored name (preserving its original casing) if `name`
    already exists under any casing, else None -- so adding "Aww" when
    "aww" is already tracked re-categorizes the existing row instead of
    creating a case-variant duplicate PRAW would treat as the same sub."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT name FROM subreddits WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
    return row["name"] if row else None


def upsert_subreddit(name: str, category: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO subreddits (name, category, added_at) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET category = excluded.category",
            (name, category, utcnow_iso()),
        )


def delete_subreddit(name: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM subreddits WHERE name = ?", (name,))


# --- tiktok_hashtags (the live, dashboard-editable TikTok search term list) --

def has_any_hashtags() -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM tiktok_hashtags LIMIT 1").fetchone()
    return row is not None


def bulk_seed_hashtags(mapping: dict) -> None:
    """INSERT OR IGNORE every (hashtag, category) pair -- used once, when the
    table is empty, to seed it from scraper.tiktok_hashtags' default list."""
    now = utcnow_iso()
    with get_conn() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO tiktok_hashtags (hashtag, category, added_at) VALUES (?, ?, ?)",
            [(tag, category, now) for tag, category in mapping.items()],
        )


def list_hashtags(category: str | None = None) -> list[str]:
    query = "SELECT hashtag FROM tiktok_hashtags"
    params: tuple = ()
    if category:
        query += " WHERE category = ?"
        params = (category,)
    query += " ORDER BY hashtag"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [r["hashtag"] for r in rows]


def list_hashtags_full() -> list[dict]:
    """[{hashtag, category, added_at}, ...], for the /tiktok-hashtags dashboard page."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM tiktok_hashtags ORDER BY category, hashtag COLLATE NOCASE").fetchall()
    return [dict(r) for r in rows]


def find_hashtag_case_insensitive(tag: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT hashtag FROM tiktok_hashtags WHERE hashtag = ? COLLATE NOCASE", (tag,)
        ).fetchone()
    return row["hashtag"] if row else None


def upsert_hashtag(tag: str, category: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO tiktok_hashtags (hashtag, category, added_at) VALUES (?, ?, ?) "
            "ON CONFLICT(hashtag) DO UPDATE SET category = excluded.category",
            (tag, category, utcnow_iso()),
        )


def delete_hashtag(tag: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM tiktok_hashtags WHERE hashtag = ?", (tag,))


# --- search_terms (the unified, dashboard-editable search-parameter list,
# shared across Reddit keyword search / TikTok / Instagram hashtag search) --

def has_any_search_terms() -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM search_terms LIMIT 1").fetchone()
    return row is not None


def bulk_seed_search_terms(mapping: dict) -> None:
    now = utcnow_iso()
    with get_conn() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO search_terms (term, category, added_at) VALUES (?, ?, ?)",
            [(term, category, now) for term, category in mapping.items()],
        )


def list_search_terms(category: str | None = None) -> list[str]:
    query = "SELECT term FROM search_terms"
    params: tuple = ()
    if category:
        query += " WHERE category = ?"
        params = (category,)
    query += " ORDER BY term"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [r["term"] for r in rows]


def list_search_terms_full() -> list[dict]:
    """[{term, category, added_at}, ...], for the /search-parameters dashboard page."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM search_terms ORDER BY category, term COLLATE NOCASE").fetchall()
    return [dict(r) for r in rows]


def find_search_term_case_insensitive(term: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT term FROM search_terms WHERE term = ? COLLATE NOCASE", (term,)
        ).fetchone()
    return row["term"] if row else None


def upsert_search_term(term: str, category: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO search_terms (term, category, added_at) VALUES (?, ?, ?) "
            "ON CONFLICT(term) DO UPDATE SET category = excluded.category",
            (term, category, utcnow_iso()),
        )


def delete_search_term(term: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM search_terms WHERE term = ?", (term,))


# --- adaptive vote tracking (scraper/adaptive.py) --------------------------

def bump_tag_vote(tag: str, category: str | None, liked: bool) -> tuple[int, int]:
    """Increments a tag's like or dislike counter and returns (likes, dislikes)."""
    col = "likes" if liked else "dislikes"
    with get_conn() as conn:
        conn.execute(
            f"INSERT INTO tag_votes (tag, category, likes, dislikes) VALUES (?, ?, {1 if liked else 0}, {0 if liked else 1}) "
            f"ON CONFLICT(tag) DO UPDATE SET {col} = {col} + 1, category = excluded.category",
            (tag, category),
        )
        row = conn.execute("SELECT likes, dislikes FROM tag_votes WHERE tag = ?", (tag,)).fetchone()
    return (row["likes"], row["dislikes"]) if row else (0, 0)


def bump_subreddit_vote(name: str, liked: bool) -> tuple[int, int]:
    col = "likes" if liked else "dislikes"
    with get_conn() as conn:
        conn.execute(
            f"INSERT INTO subreddit_votes (name, likes, dislikes) VALUES (?, {1 if liked else 0}, {0 if liked else 1}) "
            f"ON CONFLICT(name) DO UPDATE SET {col} = {col} + 1",
            (name,),
        )
        row = conn.execute("SELECT likes, dislikes FROM subreddit_votes WHERE name = ?", (name,)).fetchone()
    return (row["likes"], row["dislikes"]) if row else (0, 0)


# --- engagement scoring (scraper/engagement.py) ----------------------------

def get_engagement_stats(source: str) -> dict:
    """{"likes": (mean, stddev), "comments": (...), "shares": (...)} across
    every clip ever pulled from `source`, for z-score normalization."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT likes, comments, shares FROM clips WHERE source = ?", (source,)
        ).fetchall()
    stats = {}
    for field in ("likes", "comments", "shares"):
        values = [r[field] for r in rows if r[field] is not None]
        if len(values) >= 2:
            stats[field] = (statistics.mean(values), statistics.pstdev(values))
        elif values:
            stats[field] = (values[0], 0.0)
        else:
            stats[field] = (0.0, 0.0)
    return stats


def set_clip_engagement_score(clip_id: int, score: float) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE clips SET engagement_score = ? WHERE id = ?", (score, clip_id))


def get_available_clips_for_autobuild() -> list[dict]:
    """Triaged, not-yet-used clips with a computed engagement score,
    highest first -- the pool compiler/auto_build.py picks from."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM clips WHERE triaged = 1 AND status = 'new' AND duration_sec IS NOT NULL "
            "AND engagement_score IS NOT NULL ORDER BY engagement_score DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# --- triage_stats (per-source approval/rejection tracking, see app.py's
# /triage/keep and /triage/reject, and the /stats dashboard page) ----------

def _triage_subgroup(source: str, subreddit: str | None) -> str:
    return subreddit if source == "reddit" and subreddit else "all"


def record_triage_keep(source: str, subreddit: str | None = None) -> None:
    subgroup = _triage_subgroup(source, subreddit)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO triage_stats (source, subgroup, keeps, rejects) VALUES (?, ?, 1, 0) "
            "ON CONFLICT(source, subgroup) DO UPDATE SET keeps = keeps + 1",
            (source, subgroup),
        )


def record_triage_reject(source: str, subreddit: str | None = None) -> None:
    subgroup = _triage_subgroup(source, subreddit)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO triage_stats (source, subgroup, keeps, rejects) VALUES (?, ?, 0, 1) "
            "ON CONFLICT(source, subgroup) DO UPDATE SET rejects = rejects + 1",
            (source, subgroup),
        )


def get_triage_stats() -> list[dict]:
    """Every (source, subgroup) row with keeps/rejects/total/approval_rate,
    sorted worst-approval-rate-first within each source."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM triage_stats").fetchall()
    stats = []
    for r in rows:
        keeps, rejects = r["keeps"], r["rejects"]
        total = keeps + rejects
        approval_rate = (keeps / total) if total else None
        stats.append({
            "source": r["source"],
            "subgroup": r["subgroup"],
            "keeps": keeps,
            "rejects": rejects,
            "total": total,
            "approval_rate": approval_rate,
        })
    stats.sort(key=lambda s: (s["approval_rate"] if s["approval_rate"] is not None else 1.0))
    return stats


def _migrate_scrape_runs_fk():
    """
    scrape_runs.clip_id originally had a plain
    FOREIGN KEY (clip_id) REFERENCES clips(id) with no ON DELETE clause
    (defaults to NO ACTION). That blocks deleting a clip once it has
    ever been logged as a scrape_runs.clip_id, raising
    sqlite3.IntegrityError: FOREIGN KEY constraint failed on the
    /triage reject -> db.delete_clip path. Rebuilds scrape_runs with
    ON DELETE SET NULL instead. SQLite can't ALTER an existing foreign
    key, so this uses the documented table-rebuild pattern, and is a
    no-op if the fix is already applied.
    """
    with get_conn() as conn:
        fk_rows = conn.execute("PRAGMA foreign_key_list(scrape_runs)").fetchall()
        already_fixed = any(
            row["table"] == "clips" and (row["on_delete"] or "").upper() == "SET NULL"
            for row in fk_rows
        )
        if already_fixed:
            return

        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")
        try:
            conn.execute(
                """
                CREATE TABLE scrape_runs_new (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at          TEXT    NOT NULL,
                    candidates_seen INTEGER NOT NULL DEFAULT 0,
                    clip_id         INTEGER,
                    note            TEXT,
                    FOREIGN KEY (clip_id) REFERENCES clips(id) ON DELETE SET NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO scrape_runs_new (id, run_at, candidates_seen, clip_id, note) "
                "SELECT id, run_at, candidates_seen, clip_id, note FROM scrape_runs"
            )
            conn.execute("DROP TABLE scrape_runs")
            conn.execute("ALTER TABLE scrape_runs_new RENAME TO scrape_runs")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
