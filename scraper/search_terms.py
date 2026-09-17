"""
Unified "Search Parameters" list -- one set of search terms shared by all
three platforms (Reddit keyword search, TikTok hashtag/keyword search,
Instagram hashtag/keyword search) instead of separate per-platform lists
(the old scraper/subreddits.py + scraper/tiktok_hashtags.py split).

db.py's `search_terms` table is the live source of truth every scrape run
actually reads (add_term()/remove_term() below just write through to it).
DEFAULT_SEARCH_TERMS is only the one-time seed used the first time that
table is empty (a fresh install, or an existing install whose old
tiktok_hashtags table also happened to be empty -- see db.py's
_migrate(), which folds any already-curated TikTok hashtags in here
first) -- editing this dict later has no effect on an existing install;
use the dashboard's Search Parameters page (or add_term/remove_term
directly) instead.

Deliberately broad on purpose -- scraper/adaptive.py prunes/grows this
list over time based on triage keep/reject votes (config.ADAPTIVE_VOTE_THRESHOLD).
"""
import db
from scraper.subreddits import CATEGORIES

_FUNNY_VIRAL_TERMS = [
    "meme", "viral", "funny", "relatable", "comedy", "dank memes",
    "funny videos", "humor", "LOL", "satisfying", "oddly satisfying",
    "wholesome", "cursed", "savage", "roast", "compilation",
    "try not to laugh",
]
_FAILS_TERMS = ["fail", "fails", "epic fail", "cringe", "instant karma"]
_POLITICAL_SATIRE_TERMS = [
    "political satire", "political memes", "politics meme",
    "election meme", "JD Vance memes",
]

DEFAULT_SEARCH_TERMS = {}
DEFAULT_SEARCH_TERMS.update({t: "funny-viral" for t in _FUNNY_VIRAL_TERMS})
DEFAULT_SEARCH_TERMS.update({t: "fails" for t in _FAILS_TERMS})
DEFAULT_SEARCH_TERMS.update({t: "political-satire" for t in _POLITICAL_SATIRE_TERMS})


_SEEDED_FLAG = "search_terms_seeded"


def _ensure_seeded():
    """Seeds DEFAULT_SEARCH_TERMS exactly once, tracked by a flag in the
    settings table rather than by "is the table empty?".

    Two reasons for the flag. First, an existing droplet upgrading to the
    unified list already has rows in `search_terms` (db.py's _migrate()
    folds its curated TikTok hashtags in), so an emptiness check would
    skip the starter set entirely and leave it with just a handful of old
    hashtags. Second, an emptiness check would silently re-seed all 27
    starter terms the moment the adaptive system (or you) pruned the list
    down to nothing -- which is a legitimate state to be in, not a reason
    to undo the pruning.

    The seed itself is INSERT OR IGNORE, so it never overwrites the
    category on a term that's already there."""
    if db.get_setting_value(_SEEDED_FLAG):
        return
    db.bulk_seed_search_terms(DEFAULT_SEARCH_TERMS)
    db.set_setting(_SEEDED_FLAG, "1")


def list_all_terms() -> list[str]:
    """Every tracked search term -- what the hourly scraper feeds to all
    three platforms."""
    _ensure_seeded()
    return db.list_search_terms()


def all_terms_with_categories() -> list[dict]:
    """[{term, category, added_at}], grouped by category then term -- for
    the dashboard's /search-parameters page."""
    _ensure_seeded()
    return db.list_search_terms_full()


def terms_for_category(category: str) -> list[str]:
    _ensure_seeded()
    return db.list_search_terms(category=category)


def add_term(term: str, category: str) -> str:
    """Adds a search term (or re-categorizes it, if already tracked, case-
    insensitively). Returns the term as stored. Raises ValueError with a
    message safe to show the user on a bad term/category."""
    term = (term or "").strip()
    if not term:
        raise ValueError("Search term can't be empty.")
    if category not in CATEGORIES:
        raise ValueError(f"Unknown category {category!r}.")

    _ensure_seeded()
    existing = db.find_search_term_case_insensitive(term)
    stored_term = existing or term
    db.upsert_search_term(stored_term, category)
    return stored_term


def remove_term(term: str) -> None:
    db.delete_search_term(term)
