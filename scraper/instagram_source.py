"""
Instagram candidate source -- Bright Data's Instagram Scraper API.

Replaces the old Vimeo Bright Data leg. Instagram Reels are the closest
fit to what we're building here (short vertical clips), so this hits
Bright Data's Instagram "Discover by keyword/hashtag" collector, restricted
to reels -- the query rotation below is the direct equivalent of Vimeo's
old per-category keyword rotation, just fed through Bright Data's
trigger/poll/fetch dataset flow (scraper/brightdata_client.py).

Per run this sends the hour-rotated built-in query below PLUS that
category's terms from the unified Search Parameters list
(scraper/search_terms.py -- the same list Reddit and TikTok search), all
in one trigger/poll/fetch round trip.
"""
import logging
import re
from datetime import datetime, timezone

import config
from scraper.brightdata_client import run_collection
from scraper.candidate import Candidate
from scraper.search_terms import terms_for_category

log = logging.getLogger("meme_pipeline.instagram")

# Revised for the current content focus (funny/viral memes, fails, political
# satire) -- animal/cute and gaming are de-emphasized.
SEARCH_QUERIES = [
    ("fail compilation", "fails"),
    ("funny meme reel", "funny-viral"),
    ("satisfying video", "funny-viral"),
    ("wholesome moment", "funny-viral"),
    ("political satire meme", "political-satire"),
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


def _parse_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _candidate_from_row(row: dict, category: str) -> Candidate | None:
    # Only reels have the short-vertical-clip shape this pipeline wants --
    # skip static image posts and carousels.
    post_type = str(_first(row, "post_type", "product_type", "type", default="")).lower()
    if post_type and post_type not in ("reel", "clips", "video"):
        return None

    # Bug fix: this used to default a missing duration field to 0 and then
    # range-check it, so if the dataset's field name didn't match one of the
    # ones we try, EVERY row silently failed the range check (0 is always
    # below MIN_CLIP_DURATION_SEC) and got filtered out -- not just the rows
    # that were genuinely too long/short. Check the field actually exists
    # first; only apply the duration filter when we actually have a duration
    # to check, so a missing/renamed field just skips this filter for that
    # item instead of nuking the whole batch.
    duration_raw = _first(row, "video_duration", "duration", default=None)
    if duration_raw is not None:
        duration = _to_float(duration_raw)
        if not (config.MIN_CLIP_DURATION_SEC <= duration <= config.MAX_CLIP_DURATION_SEC):
            return None

    shortcode = _first(row, "shortcode", "post_id", "id")
    link = _first(row, "url", "link", "post_url")
    if not shortcode and link:
        m = re.search(r"/(?:reel|p)/([\w-]+)", str(link))
        shortcode = m.group(1) if m else None
    if not shortcode:
        return None
    link = link or f"https://www.instagram.com/reel/{shortcode}/"

    views = _first(row, "video_play_count", "views", "play_count", "num_views", default=0)
    likes = _first(row, "likes", "like_count", default=0)
    comments = _first(row, "comments", "comment_count", default=0)
    return Candidate(
        source="instagram",
        source_id=str(shortcode),
        source_url=link,
        media_url=link,
        category=category,
        title=_first(row, "caption", "title", "description", default=""),
        author=_first(row, "owner_username", "username", "author", default=""),
        score=_to_float(views),
        likes=_to_float(likes),
        comments=_to_float(comments),
        shares=0.0,  # Instagram's Bright Data dataset doesn't expose a share count
        published_at=_parse_datetime(_first(row, "date_posted", "timestamp", "published_at")),
    )


def _search(queries: list[str], category: str) -> list[Candidate]:
    """One reels-only keyword input per query, all in one collection run."""
    queries = list(dict.fromkeys(q for q in queries if q))  # de-dup, preserve order
    if not queries:
        return []
    inputs = [{"keyword": q, "post_type": "reel"} for q in queries]
    rows = run_collection(config.BRIGHTDATA_INSTAGRAM_DATASET_ID, inputs)
    candidates = [c for c in (_candidate_from_row(row, category) for row in rows) if c]
    log.info("Instagram: gathered %d candidates for %d query/queries (category=%s)",
              len(candidates), len(queries), category)
    return candidates


def gather_candidates() -> list[Candidate]:
    """Hourly job: the hour-rotated built-in query plus that category's
    terms from the unified Search Parameters list."""
    query, category = SEARCH_QUERIES[datetime.now(timezone.utc).hour % len(SEARCH_QUERIES)]
    return _search([query] + terms_for_category(category), category)


def gather_for_category(category: str) -> list[Candidate]:
    """Manual test-snapshot: this category's built-in query (if it has one)
    plus its unified search terms, regardless of the hour."""
    query = QUERY_BY_CATEGORY.get(category)
    return _search(([query] if query else []) + terms_for_category(category), category)
