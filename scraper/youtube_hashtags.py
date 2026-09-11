"""
Curated list of YouTube hashtag/keyword search terms, mapped to one of the
six fixed output categories -- same role for the YouTube Bright Data
source that scraper/subreddits.py plays for Reddit, and editable the same
way (a dashboard page backed by a DB table, not a code change).

db.py's `youtube_hashtags` table is the live source of truth every scrape
run actually reads (add_hashtag()/remove_hashtag() below just write
through to it). DEFAULT_HASHTAG_CATEGORY is only the one-time seed used
the first time that table is empty -- editing this dict later has no
effect on an existing install; use the dashboard (or add_hashtag/
remove_hashtag directly) instead.

These are used *in addition to* youtube_source.py's SEARCH_QUERIES, not
instead of them -- both feed the hour-rotated per-category search leg.
"""
import db
from scraper.subreddits import CATEGORIES

DEFAULT_HASHTAG_CATEGORY = {
    "#epicfail": "fails",
    "#fail": "fails",
    "#animals": "animals",
    "#cuteanimals": "animals",
    "#gaming": "gaming",
    "#gamingclips": "gaming",
    "#wholesome": "wins",
    "#nextlevel": "wins",
    "#oddlysatisfying": "oddly-satisfying",
    "#satisfying": "oddly-satisfying",
    "#mildlyinfuriating": "mildly-infuriating",
}


def _ensure_seeded():
    if not db.has_any_hashtags():
        db.bulk_seed_hashtags(DEFAULT_HASHTAG_CATEGORY)


def list_all_hashtags() -> list[str]:
    _ensure_seeded()
    return db.list_hashtags()


def all_hashtags_with_categories() -> list[dict]:
    """[{hashtag, category, added_at}], grouped by category then hashtag --
    for the dashboard's /youtube-hashtags page."""
    _ensure_seeded()
    return db.list_hashtags_full()


def hashtags_for_category(category: str) -> list[str]:
    _ensure_seeded()
    return db.list_hashtags(category=category)


def add_hashtag(tag: str, category: str) -> str:
    """Adds a hashtag (or re-categorizes it, if already tracked, case-
    insensitively). Returns the tag as stored. Raises ValueError with a
    message safe to show the user on a bad tag/category."""
    tag = (tag or "").strip()
    if not tag:
        raise ValueError("Hashtag can't be empty.")
    if any(c.isspace() for c in tag):
        raise ValueError("Hashtags can't contain spaces.")
    if not tag.startswith("#"):
        tag = f"#{tag}"
    if category not in CATEGORIES:
        raise ValueError(f"Unknown category {category!r}.")

    _ensure_seeded()
    existing = db.find_hashtag_case_insensitive(tag)
    stored_tag = existing or tag
    db.upsert_hashtag(stored_tag, category)
    return stored_tag


def remove_hashtag(tag: str) -> None:
    db.delete_hashtag(tag)
