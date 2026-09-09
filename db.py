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
    cols = ", ".join(fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    with get_conn() as conn:
        cur = conn.execute(
            f"INSERT INTO clips ({cols}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        return cur.lastrowid


def get_clips_for_date(date_str: str):
    """date_str: 'YYYY-MM-DD' (matches the /media/<date>/ folder convention)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM clips WHERE fetched_at LIKE ? ORDER BY fetched_at DESC",
            (f"{date_str}%",),
        ).fetchall()
    return [dict(r) for r in rows]


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
