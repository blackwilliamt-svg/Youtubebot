"""
TikTok candidate source -- Bright Data's TikTok Scraper API.

Replaces the old YouTube Bright Data leg. TikTok has no equivalent of a
"chart" API for us to hit, so discovery is entirely keyword/hashtag-driven,
via Bright Data's "Discover by keyword" TikTok collector:

1. A broad "trending <category>" keyword search for each of the five
   categories that have a good generic trending query (mildly-infuriating
   is deliberately excluded here too -- no good generic query for it).
2. One hour-rotated category-specific query (SEARCH_QUERIES below) PLUS
   that category's curated hashtags (scraper/tiktok_hashtags.py -- editable
   from the dashboard's /tiktok-hashtags page), each fed to the scraper as
   its own keyword input.

All of the above collapses into one Bright Data trigger/poll/fetch round
trip per run (scraper/brightdata_client.py) -- one API call covers every
keyword/hashtag input.

Note on licensing: TikTok has no Creative Commons concept the way YouTube
did, so there's no license filter here -- every result that clears the
duration window is eligible.
"""
import logging
import re
from datetime import datetime, timezone

import config
from scraper.brightdata_client import run_collection
from scraper.candidate import Candidate
from scraper.tiktok_hashtags import hashtags_for_category

log = logging.getLogger("meme_pipeline.tiktok")

# category -> a generic "trending" query, run every hour for every category
# that has one (mirrors the old YouTube-chart category coverage).
TRENDING_QUERY_BY_CATEGORY = {
    "animals": "trending animal video",
    "gaming": "trending gaming clip",
    "wins": "trending sports highlight",
    "fails": "trending comedy fail compilation",
    "oddly-satisfying": "trending oddly satisfying video",
}

# (search query, category) -- one is used per hourly run, rotated by hour.
# mildly-infuriating has no good generic trending query, so it only gets
# covered through this rotated leg (plus its hashtags), not the map above.
SEARCH_QUERIES = [
    ("epic fail compilation clip", "fails"),
    ("oddly satisfying clip", "oddly-satisfying"),
    ("amazing animal moment clip", "animals"),
    ("insane gaming clip", "gaming"),
    ("wholesome win moment clip", "wins"),
    ("mildly infuriating moment clip", "mildly-infuriating"),
]
QUERY_BY_CATEGORY = {cat: q for q, cat in SEARCH_QUERIES}


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


def _duration_seconds(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    m = re.match(r"^(?:(\d+):)?(\d+):(\d+)$", str(value))  # "H:MM:SS" or "MM:SS"
    if m:
        hours, minutes, seconds = (int(g) if g else 0 for g in m.groups())
        return hours * 3600 + minutes * 60 + seconds
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _candidate_from_row(row: dict, category: str) -> Candidate | None:
    vid = _first(row, "video_id", "id", "aweme_id")
    url = _first(row, "url", "video_url", "share_url", "webVideoUrl")
    if not vid and url:
        m = re.search(r"/video/(\d+)", str(url))
        vid = m.group(1) if m else None
    if not vid:
        return None

    duration = _duration_seconds(_first(row, "video_duration", "duration", "length"))
    if duration and not (config.MIN_CLIP_DURATION_SEC <= duration <= config.MAX_CLIP_DURATION_SEC):
        return None

    video_url = url or f"https://www.tiktok.com/@{_first(row, 'author', 'username', default='i')}/video/{vid}"
    views = _first(row, "play_count", "views", "playCount", "num_views", default=0)
    return Candidate(
        source="tiktok",
        source_id=str(vid),
        source_url=video_url,
        media_url=video_url,
        category=category,
        title=_first(row, "title", "description", "desc", "text", default=""),
        author=_first(row, "author", "username", "authorMeta", "channel_name", default=""),
        score=_to_float(views),
        published_at=_parse_datetime(_first(row, "create_time", "createTime", "upload_date", "date_posted")),
    )


def _run_batch(inputs: list[dict]) -> list[dict]:
    return run_collection(config.BRIGHTDATA_TIKTOK_DATASET_ID, inputs)


def _inputs_for_category(category: str, query: str | None) -> list[tuple[dict, str]]:
    """[(bright-data-input, category), ...] for one category's query + its curated hashtags."""
    pairs = []
    if query:
        pairs.append(({"keyword": query}, category))
    for tag in hashtags_for_category(category):
        pairs.append(({"keyword": tag}, category))
    return pairs


def _gather(category_query_pairs: list[tuple[str, str | None]]) -> list[Candidate]:
    input_pairs: list[tuple[dict, str]] = []
    for category, query in category_query_pairs:
        input_pairs += _inputs_for_category(category, query)
    if not input_pairs:
        return []

    inputs = [pair[0] for pair in input_pairs]
    rows = _run_batch(inputs)

    # Bright Data doesn't guarantee it echoes our input keyword back per row,
    # so results are matched to a category via the dataset's own
    # "input"/keyword echo field when present, falling back to the first
    # input's category if the dataset genuinely gives us nothing to go on.
    candidates = []
    fallback_category = input_pairs[0][1]
    keyword_to_category = {inp["keyword"]: cat for inp, cat in input_pairs}
    for row in rows:
        keyword = _first(row, "input_keyword", "keyword", "search_query", "query")
        category = keyword_to_category.get(keyword, fallback_category)
        cand = _candidate_from_row(row, category)
        if cand:
            candidates.append(cand)
    return candidates


def gather_candidates() -> list[Candidate]:
    """Hourly job: trending query per mapped category + one hour-rotated category's query+hashtags."""
    pairs = [(cat, q) for cat, q in TRENDING_QUERY_BY_CATEGORY.items()]
    rotated_query, rotated_category = SEARCH_QUERIES[datetime.now(timezone.utc).hour % len(SEARCH_QUERIES)]
    pairs.append((rotated_category, rotated_query))
    candidates = _gather(pairs)
    log.info("TikTok: gathered %d candidates", len(candidates))
    return candidates


def gather_for_category(category: str) -> list[Candidate]:
    """Manual test-snapshot: trending query (if mapped) + this category's query+hashtags, regardless of the hour."""
    pairs = []
    trending_query = TRENDING_QUERY_BY_CATEGORY.get(category)
    if trending_query:
        pairs.append((category, trending_query))
    query = QUERY_BY_CATEGORY.get(category)
    pairs.append((category, query))
    candidates = _gather(pairs)
    log.info("TikTok: gathered %d candidates for category=%s", len(candidates), category)
    return candidates
