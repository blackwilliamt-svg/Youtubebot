#!/usr/bin/env python3
"""
Entrypoint for the weekly feedback-loop batch build (see
deploy/meme-weekly-review.timer). Unlike the auto-build pass, this one
isn't gated by the autonomy dial -- it always builds this week's review
batch if one doesn't already exist, since surfacing clips for a
deliberate vote is useful at any autonomy level. Voting itself only ever
happens from the dashboard's /weekly-review page.

    python -m scraper.run_weekly_review
"""
import logging

import config
import db
from scraper.weekly_review import build_weekly_batch

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("meme_pipeline.run_weekly_review")


def main():
    db.init_db()
    batch_id = build_weekly_batch()
    if batch_id:
        log.info("Weekly review batch id=%d is ready.", batch_id)
    else:
        log.info("No clips available for a weekly review batch this week.")


if __name__ == "__main__":
    main()
