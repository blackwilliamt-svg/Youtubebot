"""
Curated list of trending meme/fail/video subreddits, mapped to one of the
six fixed output categories used for the /media/YYYY-MM-DD/<category>/
folder layout, the reaction-library folders, and the dashboard grouping.

The list itself is editable from the dashboard's /subreddits page --
db.py's `subreddits` table is the live source of truth every scrape run
actually reads (add_subreddit()/remove_subreddit() below just write
through to it). DEFAULT_SUBREDDIT_CATEGORY is only the one-time seed used
the first time that table is empty (a fresh install, or someone starting
from a blank DB) -- editing this dict later has no effect on an existing
install; use the dashboard (or add_subreddit/remove_subreddit directly)
instead.

CATEGORIES, unlike the subreddit list, is intentionally NOT editable here:
it's wired into the reaction-library folder structure (library.py), the
YouTube/Vimeo source query rotation, and every by-category grouping in
the dashboard, so an arbitrary new category name would need matching
changes in several other places to actually work end to end.
"""
import db

CATEGORIES = ["fails", "animals", "gaming", "wins", "oddly-satisfying", "mildly-infuriating"]

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
    # --- animals ----------------------------------------------------------
    "AnimalsBeingDerps": "animals",
    "AnimalsBeingBros": "animals",
    "AnimalsBeingJerks": "animals",
    "Zoomies": "animals",
    "aww": "animals",
    "awwducational": "animals",
    # --- gaming -------------------------------------------------------
    "gaming": "gaming",
    "GamePhysics": "gaming",
    "gamingmemes": "gaming",
    "outside": "gaming",  # IRL-as-a-game meme clips, consistently video-heavy
    # --- wins ------------------------------------------------------------
    "nextfuckinglevel": "wins",
    "HumansBeingBros": "wins",
    "MadeMeSmile": "wins",
    "ContagiousLaughter": "wins",
    "toptalent": "wins",
    # --- oddly satisfying --------------------------------------------
    "oddlysatisfying": "oddly-satisfying",
    "perfectlycutscreams": "oddly-satisfying",
    "BeAmazed": "oddly-satisfying",
    "Damnthatsinteresting": "oddly-satisfying",
    # --- mildly infuriating --------------------------------------------
    "mildlyinfuriating": "mildly-infuriating",
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
