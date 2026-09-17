#!/usr/bin/env python3
"""
Entrypoint for the periodic auto-compile pass (see deploy/meme-autobuild.timer).
Only takes any action based on the clip-selection trust dial (autonomy.py,
set from the dashboard's /settings page):

    manual      -- skip entirely; compilations are user-assembled via /build.
    assisted    -- log whether enough top-ranked material exists for a
                   60-90s compilation, but don't build it.
    autonomous  -- actually build (never upload) one via compiler.auto_build.

    python -m scraper.run_autobuild
"""
import logging

import autonomy
import config
import db
from compiler.auto_build import AutoBuildError, build_auto_compilation, select_clips_for_compilation

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("meme_pipeline.run_autobuild")


def main():
    db.init_db()
    level = autonomy.get_level()

    if level == "manual":
        log.info("Autonomy level is 'manual' -- skipping auto-build (assemble one by hand from /build).")
        return

    if level == "assisted":
        clips = select_clips_for_compilation()
        if clips:
            total = sum(c.get("duration_sec") or 0.0 for c in clips)
            log.info("Autonomy level is 'assisted' -- %d clip(s) (~%.0fs) ready for a compilation; "
                      "build it by hand from /build when you're ready.", len(clips), total)
        else:
            log.info("Autonomy level is 'assisted' -- not enough qualifying material yet.")
        return

    try:
        result = build_auto_compilation()
    except AutoBuildError:
        log.exception("Autonomous auto-build failed")
        return
    if result:
        log.info("Autonomous auto-build produced compilation id=%d.", result["id"])


if __name__ == "__main__":
    main()
