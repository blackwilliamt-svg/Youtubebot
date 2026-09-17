#!/usr/bin/env python3
"""
Entrypoint for the hourly scrape. Invoked by the meme-scrape systemd timer
(see deploy/meme-scrape.timer) -- gathers candidates from every source,
then pulls ONE video per platform (Reddit, TikTok, Instagram), each
ranked against just that platform's own candidates. That's ~3/hour,
~2,160/month -- comfortably under a 3,000/month target and under Bright
Data's 5,000 free-tier credits. Falls through to the next-best candidate
per platform if the top pick fails to download (dead link, geo-block,
etc.) rather than losing the hour for that platform.

    python -m scraper.run_hourly
"""
import logging
import sys

import config
import db
from lockutil import pipeline_lock
from scraper import instagram_source, rank, reddit_source, tiktok_source

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("meme_pipeline.run_hourly")

SOURCES = (
    ("reddit", reddit_source),
    ("tiktok", tiktok_source),
    ("instagram", instagram_source),
)


def run():
    db.init_db()
    for label, source_module in SOURCES:
        try:
            candidates = source_module.gather_candidates()
        except Exception:
            log.exception("Source %s raised unexpectedly; continuing without it", source_module.__name__)
            continue
        video_candidates = [c for c in candidates if c.media_type == "video"]
        log.info("%s: %d video candidate(s) this hour", label, len(video_candidates))
        rank.pull_top_candidate(video_candidates, f"hourly {label} video")


def main():
    with pipeline_lock() as acquired:
        if not acquired:
            log.warning("Another run_hourly (or a compilation build) is already in progress; skipping this run.")
            sys.exit(0)
        run()


if __name__ == "__main__":
    main()
