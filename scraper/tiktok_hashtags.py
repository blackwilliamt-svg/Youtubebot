"""
DEPRECATED -- kept only so an existing droplet's already-curated TikTok
hashtags aren't lost.

The per-platform hashtag list this module used to own is now part of the
single unified search-term list (scraper/search_terms.py, the dashboard's
Search Parameters page), which Reddit, TikTok and Instagram all read
from. db.py's _migrate() folds any rows still sitting in the legacy
`tiktok_hashtags` table into `search_terms` on first startup after this
change; nothing in the live pipeline reads this module or that table any
more.

The DB helpers for the legacy table still live in db.py (has_any_hashtags,
list_hashtags, upsert_hashtag, ...) for that migration's sake. There's
deliberately no seed dict here any more -- the old one mapped hashtags to
the retired six-category scheme (animals/gaming/wins/...), so seeding from
it would now fail validation against scraper.subreddits.CATEGORIES.
Use scraper.search_terms instead.
"""
from scraper.search_terms import (  # noqa: F401  -- re-exported for any stale import
    add_term,
    all_terms_with_categories,
    list_all_terms,
    remove_term,
    terms_for_category,
)
