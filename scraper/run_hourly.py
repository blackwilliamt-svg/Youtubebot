#!/usr/bin/env python3
"""
Entrypoint for the hourly scrape. Invoked by the meme-scrape systemd timer
(see deploy/meme-scrape.timer) -- gathers candidates from every source,
picks the single most-trending qualifying one, downloads+compresses it,
and records it. Falls through to the next-best candidate if the winner
fails to download (dead link, geo-block, etc.) rather than losing the hour.

    python -m scraper.run_hourly
"""
import logging
import sys

import config
import db
from lockutil import pipeline_lock
from scraper import rank, reddit_source, vimeo_source, youtube_source
from scraper.downloader import DownloadError, download_and_store

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
    log.info("Total candidates this hour: %d", len(candidates))

    fresh_ranked = [c for c in candidates if not db.is_duplicate(c.source, c.source_id)]
    for c in fresh_ranked:
        rank.score_candidate(c)
    fresh_ranked.sort(key=lambda c: c.trending_score, reverse=True)

    if not fresh_ranked:
        db.log_scrape_run(len(candidates), None, "no qualifying (non-duplicate) candidate")
        log.info("No qualifying candidate this hour.")
        return

    # Try candidates best-first in case the top pick fails to download.
    for attempt, candidate in enumerate(fresh_ranked[:5], start=1):
        try:
            fields = download_and_store(candidate)
        except DownloadError as exc:
            log.warning("Attempt %d: %s/%s failed to download (%s); trying next candidate",
                        attempt, candidate.source, candidate.source_id, exc)
            continue
        except Exception:
            log.exception("Attempt %d: unexpected error downloading %s/%s; trying next candidate",
                          attempt, candidate.source, candidate.source_id)
            continue

        clip_id = db.insert_clip(**fields)
        db.log_scrape_run(len(candidates), clip_id, "")
        log.info("Stored clip id=%d (%s/%s, category=%s)", clip_id, candidate.source,
                  candidate.source_id, candidate.category)
        return

    db.log_scrape_run(len(candidates), None, "top 5 candidates all failed to download")
    log.error("All top candidates failed to download this hour.")


def main():
    with pipeline_lock() as acquired:
        if not acquired:
            log.warning("Another run_hourly (or a compilation build) is already in progress; skipping this run.")
            sys.exit(0)
        run()


if __name__ == "__main__":
    main()
