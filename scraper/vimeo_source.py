"""
Vimeo candidate source -- Bright Data's Vimeo Scraper API.

Replaces the old Vimeo API (`/videos` search with a bearer access token)
with Bright Data's Vimeo Scraper API, which supports discovery by URL and
by keyword the same way the old integration searched by keyword -- so the
query rotation and CC-filtered behavior below are the direct equivalent
of what this module did before, just fed through Bright Data's
trigger/poll/fetch dataset flow (scraper/brightdata_client.py) instead of
a direct Vimeo API call.

Vimeo's catalog skews toward short films rather than meme clips, so this
stays the smallest of the three sources -- one keyword search per hourly
run, rotated by category like the YouTube search leg.
"""
import logging
from datetime import datetime, timezone

import config
from scraper.brightdata_client import run_collection
from scraper.candidate import Candidate

log = logging.getLogger("meme_pipeline.vimeo")

SEARCH_QUERIES = [
    ("fail compilation", "fails"),
    ("satisfying", "oddly-satisfying"),
    ("funny animal", "animals"),
    ("gaming highlight", "gaming"),
    ("amazing moment", "wins"),
    ("mildly infuriating", "mildly-infuriating"),
]
QUERY_BY_CATEGORY = {cat: q for q, cat in SEARCH_QUERIES}

# Vimeo's `license` field values that count as Creative Commons.
CC_LICENSES = {"CC", "CC-BY", "CC-BY-NC", "CC-BY-NC-ND", "CC-BY-NC-SA", "CC-BY-ND", "CC-BY-SA", "CC0"}


def _first(row: dict, *keys, default=None):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return default


def _to_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _candidate_from_row(row: dict, category: str) -> Candidate | None:
    license_ = str(_first(row, "license", "license_type", default="")).upper()
    if license_ and license_ not in CC_LICENSES:
        return None

    duration = _to_float(_first(row, "duration", "video_duration", default=0))
    if not (config.MIN_CLIP_DURATION_SEC <= duration <= config.MAX_CLIP_DURATION_SEC):
        return None

    video_id = _first(row, "video_id", "id")
    link = _first(row, "url", "link")
    if not video_id and link:
        video_id = str(link).rstrip("/").rsplit("/", 1)[-1]
    if not video_id:
        return None
    link = link or f"https://vimeo.com/{video_id}"

    return Candidate(
        source="vimeo",
        source_id=str(video_id),
        source_url=link,
        media_url=link,
        category=category,
        title=_first(row, "title", "name", default=""),
        author=_first(row, "uploader", "author", "user_name", default=""),
        score=_to_float(_first(row, "plays", "num_plays", "views", default=0)),
        published_at=_parse_datetime(_first(row, "upload_date", "release_time", "published_at")),
    )


def _search(query: str, category: str) -> list[Candidate]:
    inputs = [{"keyword": query, "sort": "plays"}]
    rows = run_collection(config.BRIGHTDATA_VIMEO_DATASET_ID, inputs)
    candidates = [c for c in (_candidate_from_row(row, category) for row in rows) if c]
    log.info("Vimeo: gathered %d candidates for query %r", len(candidates), query)
    return candidates


def gather_candidates() -> list[Candidate]:
    """Hourly job: one hour-rotated search query."""
    query, category = SEARCH_QUERIES[datetime.now(timezone.utc).hour % len(SEARCH_QUERIES)]
    return _search(query, category)


def gather_for_category(category: str) -> list[Candidate]:
    """Manual test-snapshot: search using that category's query, regardless of the hour."""
    query = QUERY_BY_CATEGORY.get(category)
    if not query:
        return []
    return _search(query, category)
