"""
Adaptive search-parameter tuning, driven by /triage keep/reject votes
(app.py calls record_clip_vote() from triage_keep/triage_reject).

Two taggable dimensions, tracked and acted on the same way:
  - freeform analyzer tags (analyzer/) -> scraper.search_terms
  - Reddit subreddit-of-origin              -> scraper.subreddits

Each dimension keeps a running like/dislike count (db.bump_tag_vote /
db.bump_subreddit_vote). A flat count of config.ADAPTIVE_VOTE_THRESHOLD
(default 3) on the like side adds the tag/subreddit to its list; the same
flat count on the dislike side removes it -- intentionally a flat
threshold, not scaled by anything, so a single one-off viral (or
disliked) outlier can't skew the list by itself. The action re-fires
every time the running count crosses another multiple of the threshold
(3, 6, 9, ...), which is harmless: adding an already-present term or
removing an already-absent one is a no-op.

Votes are weighted: an ordinary /triage keep/reject is weight 1, but a
weekly-review vote (scraper/weekly_review.py) counts for more
(config.WEEKLY_REVIEW_VOTE_WEIGHT, default 3) since it's a deliberate,
reflective sample rather than an in-the-moment first pass -- and a
weekly-review vote that disagrees with the bot's own top pick for that
clip ("divergence") counts for even more
(config.WEEKLY_REVIEW_DIVERGENCE_WEIGHT, default 6). Because a single
vote can now jump the running count by more than 1, "crossed a multiple
of the threshold" can't be tested with `% == 0` any more -- a jump from
2 to 8 (weight 6) would skip right over 3 and 6 without ever landing on
one. Firing is instead based on how many multiples of the threshold the
count passed through between its value before and after this vote.
"""
import json
import logging

import config
import db
from scraper.subreddits import CATEGORIES

log = logging.getLogger("meme_pipeline.adaptive")

_DEFAULT_CATEGORY = CATEGORIES[0] if CATEGORIES else "funny-viral"


def _fires(before: int, after: int) -> bool:
    """True if `after` crossed (or landed on) a multiple of the adaptive
    threshold that `before` hadn't already reached -- i.e. the running
    count now covers at least one more multiple of the threshold than it
    did before this vote was added. Handles weighted jumps >1 correctly,
    unlike a plain `count % threshold == 0` check."""
    threshold = config.ADAPTIVE_VOTE_THRESHOLD
    if threshold <= 0:
        return False
    return (after // threshold) > (before // threshold)


def _clip_tags(clip: dict) -> list[str]:
    raw = clip.get("tags")
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def record_clip_vote(clip: dict, liked: bool, weight: int = 1) -> None:
    """Call once per /triage keep (liked=True) or reject (liked=False),
    with the full clip dict (as returned by db.get_clip / db.delete_clip).
    `weight` defaults to 1 for an ordinary triage vote; pass a larger
    weight for a weekly-review vote (see module docstring).
    Best-effort: never raises, so a DB hiccup here can't break triage."""
    if not clip:
        return
    category = clip.get("category") or _DEFAULT_CATEGORY
    weight = max(1, int(weight))

    try:
        for tag in _clip_tags(clip):
            likes, dislikes = db.bump_tag_vote(tag, category, liked, weight=weight)
            count = likes if liked else dislikes
            before = count - weight
            if liked and _fires(before, count):
                _add_search_term(tag, category, count)
            elif not liked and _fires(before, count):
                _remove_search_term(tag, count)
    except Exception:
        log.exception("Adaptive tag-vote bookkeeping failed for clip id=%s", clip.get("id"))

    if clip.get("source") == "reddit" and clip.get("subreddit"):
        try:
            name = clip["subreddit"]
            likes, dislikes = db.bump_subreddit_vote(name, liked, weight=weight)
            count = likes if liked else dislikes
            before = count - weight
            if liked and _fires(before, count):
                _add_subreddit(name, category, count)
            elif not liked and _fires(before, count):
                _remove_subreddit(name, count)
        except Exception:
            log.exception("Adaptive subreddit-vote bookkeeping failed for clip id=%s", clip.get("id"))


def _add_search_term(tag: str, category: str, likes: int) -> None:
    from scraper import search_terms
    try:
        search_terms.add_term(tag, category)
        log.info("Adaptive: added search term %r (category=%s) after %d liked clips", tag, category, likes)
    except ValueError:
        pass  # empty tag, or category somehow invalid -- not worth surfacing here


def _remove_search_term(tag: str, dislikes: int) -> None:
    from scraper import search_terms
    search_terms.remove_term(tag)
    log.info("Adaptive: removed search term %r after %d disliked clips", tag, dislikes)


def _add_subreddit(name: str, category: str, likes: int) -> None:
    from scraper import subreddits as subreddits_module
    try:
        subreddits_module.add_subreddit(name, category)
        log.info("Adaptive: added subreddit r/%s (category=%s) after %d liked clips", name, category, likes)
    except ValueError:
        pass


def _remove_subreddit(name: str, dislikes: int) -> None:
    from scraper import subreddits as subreddits_module
    subreddits_module.remove_subreddit(name)
    log.info("Adaptive: removed subreddit r/%s after %d disliked clips", name, dislikes)
