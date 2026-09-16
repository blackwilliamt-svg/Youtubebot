"""
Manual "Run Test Snapshot" button (see app.py's /snapshot/run). For each
of the 6 categories, pulls the single top-ranked video AND the single
top-ranked image/GIF currently trending in that category's subreddits
(plus TikTok/Instagram's per-category query) -- a one-time snapshot, not a
simulated 24h run. Reuses the exact same ranking (scraper/rank.py) and
per-source rate-limit backoff already used by the hourly job, and the
same 24h dedup log, so it can't double-pull something the hourly job
already grabbed. Categories are processed strictly one at a time with a
delay between them (SNAPSHOT_CATEGORY_DELAY_SEC) rather than firing all 6
API calls at once, to stay gentle on rate limits.
"""
import logging
import time
from typing import Callable, Optional

import config
import db
from lockutil import pipeline_lock
from scraper import instagram_source, rank, reddit_source, tiktok_source
from scraper.subreddits import CATEGORIES

log = logging.getLogger("meme_pipeline.snapshot")


def _gather_for_category(category: str):
    candidates = []
    for source_module in (reddit_source, tiktok_source, instagram_source):
        try:
            candidates += source_module.gather_for_category(category)
        except Exception:
            log.exception("Source %s raised unexpectedly for category=%s; continuing without it",
                          source_module.__name__, category)
    return candidates


def run_test_snapshot(progress_cb: Optional[Callable[[str, int], None]] = None) -> dict:
    """
    Returns {"pulled": N, "by_category": {category: [clip_id, ...]}} on
    success, or {"error": "..."} if the pipeline lock couldn't be acquired
    (a scrape or compilation build already in progress).
    """
    db.init_db()
    with pipeline_lock() as acquired:
        if not acquired:
            return {"error": "Pipeline busy (an hourly scrape or a compilation build is already running); try again shortly."}

        by_category = {}
        total = 0
        for i, category in enumerate(CATEGORIES):
            candidates = _gather_for_category(category)
            video_candidates = [c for c in candidates if c.media_type == "video"]
            image_candidates = [c for c in candidates if c.media_type in ("image", "gif")]

            pulled = []
            video_id = rank.pull_top_candidate(video_candidates, f"snapshot:{category}:video")
            if video_id:
                pulled.append(video_id)
            image_id = rank.pull_top_candidate(image_candidates, f"snapshot:{category}:image/gif")
            if image_id:
                pulled.append(image_id)

            by_category[category] = pulled
            total += len(pulled)
            log.info("Snapshot: category=%s pulled %d item(s)", category, len(pulled))
            if progress_cb:
                progress_cb(category, total)

            if i < len(CATEGORIES) - 1:
                time.sleep(config.SNAPSHOT_CATEGORY_DELAY_SEC)

        return {"pulled": total, "by_category": by_category}
