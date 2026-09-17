"""
Composite engagement score for ranking already-downloaded clips into a
compilation (compiler/auto_build.py) -- NOT the same thing as
scraper.rank's "trending velocity" score, which is used at *scrape* time
to pick a single winning candidate out of raw, not-yet-comparable
per-platform numbers (Reddit upvotes vs TikTok plays vs Instagram views).

This one runs once per stored clip, using each platform's own historical
mean/stddev (a z-score) across likes, comments, and shares, weighted
(config.ENGAGEMENT_WEIGHT_*, default 0.5/0.3/0.2) -- so a TikTok clip
with 40k likes isn't automatically ranked over a Reddit clip with 4k
upvotes just because TikTok's numbers run bigger across the board.

Also folds in `clips.feedback_score` (config.FEEDBACK_SCORE_WEIGHT,
default 0.5) -- the weighted human-feedback signal from the weekly
review loop (scraper/weekly_review.py). Unlike likes/comments/shares,
feedback_score is not z-scored against platform history: it's already a
small, hand-shaped +/- number (weighted votes -- see
config.WEEKLY_REVIEW_VOTE_WEIGHT/WEEKLY_REVIEW_DIVERGENCE_WEIGHT), so
it's added directly, scaled by its own weight.
"""
import logging

import config
import db

log = logging.getLogger("meme_pipeline.engagement")


def _zscore(value: float, mean: float, stddev: float) -> float:
    if stddev <= 1e-9:
        return 0.0
    return (value - mean) / stddev


def compute_and_store(clip_id: int) -> float:
    """Computes clip_id's composite engagement score against its
    platform's current historical stats and persists it on the clips
    row. Returns the score (0.0 if the clip doesn't exist)."""
    clip = db.get_clip(clip_id)
    if not clip:
        return 0.0

    stats = db.get_engagement_stats(clip["source"])
    weights = (
        ("likes", config.ENGAGEMENT_WEIGHT_LIKES),
        ("comments", config.ENGAGEMENT_WEIGHT_COMMENTS),
        ("shares", config.ENGAGEMENT_WEIGHT_SHARES),
    )
    score = 0.0
    for field, weight in weights:
        mean, stddev = stats.get(field, (0.0, 0.0))
        score += weight * _zscore(clip.get(field) or 0.0, mean, stddev)

    score += config.FEEDBACK_SCORE_WEIGHT * (clip.get("feedback_score") or 0.0)

    db.set_clip_engagement_score(clip_id, score)
    log.debug("Engagement score for clip id=%d (%s): %.3f", clip_id, clip["source"], score)
    return score


def recompute_all(source: str | None = None) -> int:
    """Recomputes every clip's engagement score against the *current*
    historical stats -- handy to run after a batch of new clips has
    shifted the per-platform mean/stddev meaningfully. Returns the count
    updated. Not wired into any automatic schedule (an occasional manual
    `python -c "from scraper.engagement import recompute_all; recompute_all()"`
    is enough for this pipeline's volume)."""
    sources = [source] if source else ["reddit", "tiktok", "instagram"]
    updated = 0
    for src in sources:
        with db.get_conn() as conn:
            ids = [r["id"] for r in conn.execute("SELECT id FROM clips WHERE source = ?", (src,)).fetchall()]
        for clip_id in ids:
            compute_and_store(clip_id)
            updated += 1
    return updated
