#!/usr/bin/env python3
"""
Entrypoint for the hourly scrape. Invoked by the meme-scrape systemd timer
(see deploy/meme-scrape.timer) -- gathers candidates from every source,
then pulls TWO items: the single most-trending video, and separately the
single most-trending image/GIF (ranked on its own scale, never compared
against video engagement numbers). ~24 video + ~24 image/GIF per day.
Falls through to the next-best candidate in each bucket if the top pick
fails to download (dead link, geo-block, etc.) rather than losing the hour.

    python -m scraper.run_hourly
"""
import logging
import sys

import config
import db
from lockutil import pipeline_lock
from scraper import rank, reddit_source, vimeo_source, youtube_source

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("meme_pipeline.run_hourly")


def gather_all_candidates():
    candidates = []
    for source_module in (reddit_source, youtube_source, vimeo_source):
        try:
            candidates += source_module.gather_candidates()
        except Exception:
            log.exception("Source %s raised unexpectedly; continuing without it", source_module.__name__)
    return candidates


def run():
    db.init_db()
    candidates = gather_all_candidates()
    video_candidates = [c for c in candidates if c.media_type == "video"]
    image_candidates = [c for c in candidates if c.media_type in ("image", "gif")]
    log.info("Total candidates this hour: %d video, %d image/gif", len(video_candidates), len(image_candidates))

    rank.pull_top_candidate(video_candidates, "hourly video")
    rank.pull_top_candidate(image_candidates, "hourly image/gif")


def main():
    with pipeline_lock() as acquired:
        if not acquired:
            log.warning("Another run_hourly (or a compilation build) is already in progress; skipping this run.")
            sys.exit(0)
        run()


if __name__ == "__main__":
    main()
