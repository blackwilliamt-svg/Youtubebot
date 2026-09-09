"""
Shared single-flight lock. The droplet only has 2GB RAM + swap, so the
hourly scrape (ffmpeg compress) and a dashboard-triggered compilation
build (ffmpeg concat/filter) must never run at the same time -- both
processes take this same lock.
"""
import contextlib
import logging

import config

log = logging.getLogger("meme_pipeline.lock")

LOCK_PATH = config.LOCK_DIR / "pipeline.lock"

try:
    import fcntl  # POSIX only -- the only platform this actually runs on (the droplet)
except ImportError:
    fcntl = None
    log.warning(
        "fcntl unavailable (not on a POSIX system) -- pipeline_lock() falls back to a "
        "non-atomic file-existence check, fine for local smoke-testing but not production."
    )


@contextlib.contextmanager
def pipeline_lock(blocking: bool = False):
    """
    Yields True if the lock was acquired, False if another process already
    holds it (only possible when blocking=False). Always use as:

        with pipeline_lock() as acquired:
            if not acquired:
                ...bail out...
    """
    config.LOCK_DIR.mkdir(parents=True, exist_ok=True)

    if fcntl is None:
        # Non-POSIX fallback: not atomic, just enough for solo local testing.
        if LOCK_PATH.exists() and not blocking:
            yield False
            return
        LOCK_PATH.touch()
        try:
            yield True
        finally:
            LOCK_PATH.unlink(missing_ok=True)
        return

    with open(LOCK_PATH, "w") as lock_file:
        flags = fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            fcntl.flock(lock_file, flags)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
