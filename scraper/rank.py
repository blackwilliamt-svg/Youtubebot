"""
Picks the single "most trending" candidate out of everything gathered this
hour, across all three platforms. Raw engagement numbers aren't comparable
across platforms (Reddit upvotes vs YouTube views vs Vimeo plays), so we
rank by *velocity* -- engagement per hour since posting -- which rewards
things that are trending right now over old high-water-mark posts, and
happens to put all three sources on roughly the same footing.
"""
import logging

import db
from scraper.candidate import Candidate

log = logging.getLogger("meme_pipeline.rank")


def score_candidate(c: Candidate) -> float:
    c.trending_score = c.score / c.hours_since_published()
    return c.trending_score


def pick_winner(candidates: list[Candidate]) -> Candidate | None:
    """
    Dedups against the rolling window, scores everything that's left, and
    returns the single highest-velocity candidate (None if nothing
    qualifies this hour).
    """
    fresh = [c for c in candidates if not db.is_duplicate(c.source, c.source_id)]
    dropped = len(candidates) - len(fresh)
    if dropped:
        log.info("Rank: dropped %d already-seen candidates (24h dedup)", dropped)

    if not fresh:
        return None

    for c in fresh:
        score_candidate(c)

    fresh.sort(key=lambda c: c.trending_score, reverse=True)
    winner = fresh[0]
    log.info(
        "Rank: winner=%s/%s score=%.2f (%d candidates considered)",
        winner.source, winner.source_id, winner.trending_score, len(fresh),
    )
    return winner
