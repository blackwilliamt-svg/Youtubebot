#!/usr/bin/env python3
"""
Optional disk-space guard: deletes the local video/thumbnail files for
clips older than RETENTION_DAYS that were never used in a compilation
(used clips are assumed to matter more and are left alone -- delete
compilations/ manually if you want those gone too). DB rows and JSON
sidecars are kept either way, so provenance history survives.

Not enabled by default -- see deploy/meme-retention.timer /
deploy/meme-retention.service and README.md if you want it running.

    python -m scraper.retention [--days N] [--dry-run]
"""
import argparse
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
import db

log = logging.getLogger("meme_pipeline.retention")


def run(days: int, dry_run: bool):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, file_path, thumb_path FROM clips "
            "WHERE fetched_at < ? AND status != 'used' AND file_path IS NOT NULL",
            (cutoff,),
        ).fetchall()

    freed = 0
    for row in rows:
        for field in ("file_path", "thumb_path"):
            p = row[field]
            if not p:
                continue
            path = Path(p)
            if path.exists():
                size = path.stat().st_size
                if dry_run:
                    log.info("[dry-run] would delete %s (%.1f MB)", path, size / 1e6)
                else:
                    path.unlink()
                    log.info("Deleted %s (%.1f MB)", path, size / 1e6)
                freed += size
        if not dry_run:
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE clips SET file_path = NULL, thumb_path = NULL WHERE id = ?",
                    (row["id"],),
                )

    log.info("Retention pass: %d clips, %.1f MB freed%s", len(rows), freed / 1e6,
              " (dry run)" if dry_run else "")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(args.days, args.dry_run)
