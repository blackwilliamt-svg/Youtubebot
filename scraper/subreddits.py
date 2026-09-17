"""
Preferred/origin Reddit subreddits -- an adaptive tracking dimension, not
a scrape-source list any more (Reddit sourcing switched from subreddit-
based to keyword-based; see scraper/reddit_source.py and
scraper/search_terms.py, which now drives what Reddit/TikTok/Instagram
actually search for). A subreddit lands here either by hand (dashboard's
Search Parameters page) or automatically once it crosses
config.ADAPTIVE_VOTE_THRESHOLD likes/dislikes in triage (scraper/adaptive.py).

db.py's `subreddits` table is the live source of truth (add_subreddit()/
remove_subreddit() below just write through to it). DEFAULT_SUBREDDIT_CATEGORY
is only the one-time seed used the first time that table is empty --
editing this dict later has no effect on an existing install.

CATEGORIES, unlike the subreddit list, is intentionally NOT editable here:
it's wired into the reaction-library folder structure (library.py), the
Reddit/TikTok/Instagram search-term rotation, and every by-category
grouping in the dashboard, so an arbitrary new category name would need
matching changes in several other places to actually work end to end.
Revised to three open-ended buckets (funny/viral memes, fails, and
political satire) -- animal/cute and gaming content is de-emphasized per
the current content focus; within each bucket, the analyzer's freeform
tags (analyzer/) carry the actual fine-grained classification instead of
a fixed sub-list.
"""
import db

CATEGORIES = ["funny-viral", "fails", "political-satire"]

DEFAULT_SUBREDDIT_CATEGORY = {
    # --- fails / chaos ---------------------------------------------------
    "PublicFreakout": "fails",
    "instant_regret": "fails",
    "Unexpected": "fails",
    "WhatCouldGoWrong": "fails",
    "IdiotsInCars": "fails",
    "ClumsyGirls": "fails",
    "Wellthatsucks": "fails",
    "therewasanattempt": "fails",
    # --- funny / viral / wholesome / satisfying ---------------------------
    "nextfuckinglevel": "funny-viral",
    "HumansBeingBros": "funny-viral",
    "MadeMeSmile": "funny-viral",
    "ContagiousLaughter": "funny-viral",
    "toptalent": "funny-viral",
    "oddlysatisfying": "funny-viral",
    "perfectlycutscreams": "funny-viral",
    "BeAmazed": "funny-viral",
    "Damnthatsinteresting": "funny-viral",
    "mildlyinfuriating": "funny-viral",
    # --- political satire --------------------------------------------------
    "PoliticalHumor": "political-satire",
    "SatiricalPolitics": "political-satire",
}


def _ensure_seeded():
    if not db.has_any_subreddits():
        db.bulk_seed_subreddits(DEFAULT_SUBREDDIT_CATEGORY)


def list_all_subreddits() -> list[str]:
    """Every tracked subreddit name, alphabetical -- what the hourly scraper
    iterates over."""
    _ensure_seeded()
    return db.list_subreddit_names()


def all_subreddits_with_categories() -> list[dict]:
    """[{name, category, added_at}], grouped by category then name -- for
    the dashboard's /subreddits page."""
    _ensure_seeded()
    return db.list_subreddits_full()


def category_for_subreddit(name: str) -> str:
    _ensure_seeded()
    return db.get_subreddit_category(name) or "fails"


def subreddits_for_category(category: str) -> list[str]:
    _ensure_seeded()
    return db.list_subreddit_names(category=category)


def add_subreddit(name: str, category: str) -> str:
    """Adds a subreddit (or re-categorizes it, if the name -- case-
    insensitively -- is already tracked). Returns the name as stored.
    Raises ValueError with a message safe to show the user on a bad
    name/category. Doesn't verify the subreddit actually exists on Reddit
    -- a typo just yields zero candidates from that source at scrape time
    (already handled gracefully there), not a crash.
    """
    name = (name or "").strip()
    if name.lower().startswith("r/"):
        name = name[2:]
    name = name.strip("/").strip()
    if not name:
        raise ValueError("Subreddit name can't be empty.")
    if any(c.isspace() for c in name):
        raise ValueError("Subreddit names can't contain spaces.")
    if category not in CATEGORIES:
        raise ValueError(f"Unknown category {category!r}.")

    _ensure_seeded()
    existing = db.find_subreddit_case_insensitive(name)
    stored_name = existing or name
    db.upsert_subreddit(stored_name, category)
    return stored_name


def remove_subreddit(name: str) -> None:
    db.delete_subreddit(name)
