"""
Curated list of trending meme/fail/video subreddits, mapped to the five
output categories used for the /media/YYYY-MM-DD/<category>/ folder layout
and the dashboard grouping. Tune freely -- this is just a starting set.
"""

SUBREDDIT_CATEGORY = {
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
}

ALL_SUBREDDITS = list(SUBREDDIT_CATEGORY.keys())
CATEGORIES = sorted(set(SUBREDDIT_CATEGORY.values()))


def category_for_subreddit(name: str) -> str:
    return SUBREDDIT_CATEGORY.get(name, "fails")
