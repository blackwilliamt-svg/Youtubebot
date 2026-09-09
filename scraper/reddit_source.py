"""
Reddit candidate source. Uses PRAW in read-only (app-only OAuth) mode --
client_id + client_secret is all that's needed, no user login, and it stays
comfortably inside the free tier's ~100 QPM as long as we don't hammer it
(we hit ~28 subreddits x 2 listings = 56 calls/hour at most).
"""
import logging
from datetime import datetime, timezone

import praw
import prawcore

import config
from scraper.candidate import Candidate
from scraper.retry import with_backoff
from scraper.subreddits import ALL_SUBREDDITS, category_for_subreddit

log = logging.getLogger("meme_pipeline.reddit")

VIDEO_DOMAINS = (
    "v.redd.it", "gfycat.com", "redgifs.com", "streamable.com",
    "youtube.com", "youtu.be", "clips.twitch.tv",
)


def _get_client():
    if not (config.REDDIT_CLIENT_ID and config.REDDIT_CLIENT_SECRET):
        log.warning("Reddit credentials not configured; skipping reddit source")
        return None
    return praw.Reddit(
        client_id=config.REDDIT_CLIENT_ID,
        client_secret=config.REDDIT_CLIENT_SECRET,
        user_agent=config.REDDIT_USER_AGENT,
    )


def _is_video_post(submission) -> bool:
    if getattr(submission, "is_video", False):
        return True
    url = getattr(submission, "url", "") or ""
    if any(domain in url for domain in VIDEO_DOMAINS):
        return True
    if url.endswith((".mp4", ".gifv", ".webm")):
        return True
    post_hint = getattr(submission, "post_hint", "")
    return post_hint in ("hosted:video", "rich:video")


def _duration_hint(submission):
    try:
        return submission.media["reddit_video"]["duration"]
    except (TypeError, KeyError, AttributeError):
        return None


@with_backoff(exceptions=(prawcore.exceptions.PrawcoreException,), max_attempts=4)
def _fetch_listing(subreddit, listing_name, limit):
    listing_fn = getattr(subreddit, listing_name)
    return list(listing_fn(limit=limit))


def gather_candidates() -> list[Candidate]:
    """Pull hot + rising from every curated subreddit and return video-only candidates."""
    reddit = _get_client()
    if reddit is None:
        return []

    candidates = []
    for name in ALL_SUBREDDITS:
        category = category_for_subreddit(name)
        try:
            subreddit = reddit.subreddit(name)
            posts = _fetch_listing(subreddit, "hot", config.REDDIT_LISTING_LIMIT)
            posts += _fetch_listing(subreddit, "rising", config.REDDIT_LISTING_LIMIT)
        except prawcore.exceptions.PrawcoreException as exc:
            log.warning("Skipping r/%s after repeated errors: %s", name, exc)
            continue

        seen_ids = set()
        for post in posts:
            if post.id in seen_ids or post.stickied or post.over_18:
                continue
            seen_ids.add(post.id)
            if not _is_video_post(post):
                continue
            duration = _duration_hint(post)
            if duration and not (
                config.MIN_CLIP_DURATION_SEC <= duration <= config.MAX_CLIP_DURATION_SEC
            ):
                continue

            candidates.append(
                Candidate(
                    source="reddit",
                    source_id=post.id,
                    source_url=f"https://www.reddit.com{post.permalink}",
                    media_url=f"https://www.reddit.com{post.permalink}",
                    category=category,
                    title=post.title,
                    author=str(post.author) if post.author else "",
                    subreddit=name,
                    score=float(post.score),
                    published_at=datetime.fromtimestamp(post.created_utc, tz=timezone.utc),
                )
            )
    log.info("Reddit: gathered %d video candidates across %d subreddits", len(candidates), len(ALL_SUBREDDITS))
    return candidates
