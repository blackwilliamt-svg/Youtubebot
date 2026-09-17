"""
Weekly feedback loop (build prompt section 7) -- once a week, a curated
batch of clips is surfaced for a deliberate keep/reject pass, distinct
from the in-the-moment /triage queue.

Two things make this different from ordinary triage:

1. The batch deliberately INCLUDES the bot's own top-ranked ("hottest")
   picks for the week (by engagement_score), rather than a purely random
   sample. That lets a vote be compared against what the bot already
   believed about the clip.
2. Votes here count for more than a routine /triage keep/reject in both
   the adaptive tag/subreddit system (scraper/adaptive.py) and the
   engagement score (scraper/engagement.py's feedback_score fold-in):
   an ordinary weekly-review vote is config.WEEKLY_REVIEW_VOTE_WEIGHT
   (default 3), and a vote that *disagrees* with the bot's own top-pick
   assessment ("divergence" -- downvoting something the bot rated as a
   top pick) is config.WEEKLY_REVIEW_DIVERGENCE_WEIGHT (default 6),
   since that's the strongest possible signal that the engagement-score
   model and the user's actual taste have drifted apart.

One batch is built per call to build_weekly_batch() (intended to be run
once a week by scraper/run_weekly_review.py + a deploy timer, but can
also be triggered manually from the dashboard). Batch size is
config.WEEKLY_REVIEW_BATCH_MIN..MAX (default 12-24): up to
config.WEEKLY_REVIEW_TOP_PICK_COUNT (default 12) top picks, backfilled
with the next-best clips from the same window up to at least the
minimum batch size.
"""
import logging
from datetime import datetime, timedelta, timezone

import config
import db
from scraper import adaptive

log = logging.getLogger("meme_pipeline.weekly_review")


def _week_label(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def build_weekly_batch(now: datetime | None = None) -> int:
    """Builds this week's review batch and returns its id. If a batch
    already exists for the current ISO week, returns that batch's id
    instead of creating a duplicate (safe to call more than once in the
    same week, e.g. a manual "build now" click after the scheduled run
    already fired)."""
    now = now or datetime.now(timezone.utc)
    label = _week_label(now)

    existing = db.get_latest_weekly_review_batch()
    if existing and existing.get("week_label") == label:
        return existing["id"]

    since = (now - timedelta(days=7)).isoformat()

    top_picks = db.get_top_clips_by_engagement(since, config.WEEKLY_REVIEW_TOP_PICK_COUNT)
    items = [{"clip_id": c["id"], "is_top_pick": True} for c in top_picks]

    remaining = max(0, config.WEEKLY_REVIEW_BATCH_MIN - len(items))
    if remaining > 0:
        fill = db.get_recent_clips_excluding(since, [c["id"] for c in top_picks], remaining)
        items += [{"clip_id": c["id"], "is_top_pick": False} for c in fill]

    items = items[: config.WEEKLY_REVIEW_BATCH_MAX]

    if not items:
        log.info("Weekly review: no clips available for batch %s, skipping", label)
        return 0

    batch_id = db.create_weekly_review_batch(label, items)
    log.info(
        "Weekly review: built batch %s (id=%d) with %d clip(s), %d top pick(s)",
        label, batch_id, len(items), sum(1 for i in items if i["is_top_pick"]),
    )
    return batch_id


def record_vote(item_id: int, liked: bool) -> None:
    """Casts a weekly-review vote for one batch item: updates the item's
    stored vote, feeds the weighted vote into the adaptive tag/subreddit
    system, and shifts the clip's feedback_score (folded into
    engagement_score by scraper/engagement.py). Best-effort against the
    adaptive/engagement side -- the vote itself is always recorded."""
    item = db.get_weekly_review_item(item_id)
    if not item:
        return

    db.record_weekly_review_vote(item_id, "up" if liked else "down")

    is_divergence = item.get("is_top_pick") and not liked
    weight = config.WEEKLY_REVIEW_DIVERGENCE_WEIGHT if is_divergence else config.WEEKLY_REVIEW_VOTE_WEIGHT

    clip = db.get_clip(item["clip_id"])
    if not clip:
        return

    try:
        adaptive.record_clip_vote(clip, liked, weight=weight)
    except Exception:
        log.exception("Weekly review: adaptive vote bookkeeping failed for item id=%s", item_id)

    try:
        delta = weight if liked else -weight
        new_score = (clip.get("feedback_score") or 0.0) + delta
        db.set_clip_feedback_score(clip["id"], new_score)
        from scraper import engagement
        engagement.compute_and_store(clip["id"])
    except Exception:
        log.exception("Weekly review: feedback-score update failed for item id=%s", item_id)

    if is_divergence:
        log.info(
            "Weekly review: DIVERGENCE vote on clip id=%d (bot top pick, user downvoted), weight=%d",
            clip["id"], weight,
        )
