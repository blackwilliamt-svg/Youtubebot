"""
Thin SQLite helper layer. One connection per call (SQLite + a small
single-box cron/Flask app doesn't need pooling); WAL mode lets the hourly
scraper write while the dashboard reads without locking each other out.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import config


def _connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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


def _migrate():
    """
    Defensive ALTER TABLEs for columns added after a DB may already have
    been created (CREATE TABLE IF NOT EXISTS in schema.sql doesn't touch
    an existing table). Safe to call every startup.
    """
    with get_conn() as conn:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(clips)")}
        if "media_type" not in existing:
            conn.execute("ALTER TABLE clips ADD COLUMN media_type TEXT NOT NULL DEFAULT 'video'")
        if "triaged" not in existing:
            conn.execute("ALTER TABLE clips ADD COLUMN triaged INTEGER NOT NULL DEFAULT 0")


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
