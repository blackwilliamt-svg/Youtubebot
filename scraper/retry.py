"""Small dependency-free exponential backoff retry decorator."""
import functools
import logging
import random
import time

log = logging.getLogger("meme_pipeline.retry")


def with_backoff(*, exceptions=(Exception,), max_attempts=5, base_delay=1.0, max_delay=30.0):
    """
    Retries the wrapped call on the given exception types with exponential
    backoff + jitter. Used around every outbound API/network call so a
    transient rate-limit or network blip doesn't kill an hourly run.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            attempt = 0
            while True:
                attempt += 1
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    if attempt >= max_attempts:
                        log.error("%s failed after %d attempts: %s", fn.__name__, attempt, exc)
                        raise
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    delay += random.uniform(0, delay * 0.25)
                    log.warning(
                        "%s attempt %d/%d failed (%s), retrying in %.1fs",
                        fn.__name__, attempt, max_attempts, exc, delay,
                    )
                    time.sleep(delay)

        return wrapper

    return decorator
