"""
Reddit candidate source. Uses PRAW in read-only (app-only OAuth) mode --
client_id + client_secret is all that's needed, no user login, and it stays
comfortably inside the free tier's ~100 QPM as long as we don't hammer it
(we hit ~29 subreddits x 2 listings = 58 calls/hour at most).

Captures three post shapes off the same hot+rising listings: video
(v.redd.it/gfycat/redgifs/streamable/embedded youtube), gif (native .gif
files), and image (jpg/png/webp). Video and image/gif are ranked on
separate velocity scales elsewhere (scraper/rank.py) -- this module just
tags each Candidate with the right media_type and leaves ranking to the
caller.
"""
import logging
from datetime import datetime, timezone

import praw
import prawcore

import config
from scraper.candidate import Candidate
from scraper.retry import with_backoff
from scraper.subreddits import category_for_subreddit, list_all_subreddits, subreddits_for_category

log = logging.getLogger("meme_pipeline.reddit")

VIDEO_DOMAINS = (
    "v.redd.it", "gfycat.com", "redgifs.com", "streamable.com",
    "youtube.com", "youtu.be", "clips.twitch.tv",
)
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


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


def _classify_post(submission) -> str | None:
    """Returns 'video' | 'gif' | 'image' | None (not a media post we handle)."""
    if getattr(submission, "is_gallery", False):
        return None  # multi-image galleries -- not handled, keep it simple
    if _is_video_post(submission):
        return "video"
    url = (getattr(submission, "url", "") or "").lower()
    if url.endswith(".gif"):
        return "gif"
    post_hint = getattr(submission, "post_hint", "")
    if post_hint == "image" or url.endswith(IMAGE_EXTS):
        return "image"
    return None


def _duration_hint(submission):
    try:
        return submission.media["reddit_video"]["duration"]
    except (TypeError, KeyError, AttributeError):
        return None


@with_backoff(exceptions=(prawcore.exceptions.PrawcoreException,), max_attempts=4)
def _fetch_listing(subreddit, listing_name, limit):
    listing_fn = getattr(subreddit, listing_name)
    return list(listing_fn(limit=limit))


def _gather_from_subreddits(reddit, subreddit_names: list[str]) -> list[Candidate]:
    candidates = []
    for name in subreddit_names:
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

            media_type = _classify_post(post)
            if media_type is None:
                continue

            if media_type == "video":
                duration = _duration_hint(post)
                if duration and not (
                    config.MIN_CLIP_DURATION_SEC <= duration <= config.MAX_CLIP_DURATION_SEC
                ):
                    continue
                media_url = f"https://www.reddit.com{post.permalink}"  # yt-dlp target
            else:
                media_url = post.url  # direct image/gif url, fetched with plain requests

            candidates.append(
                Candidate(
                    source="reddit",
                    source_id=post.id,
                    source_url=f"https://www.reddit.com{post.permalink}",
                    media_url=media_url,
                    media_type=media_type,
                    category=category,
                    title=post.title,
                    author=str(post.author) if post.author else "",
                    subreddit=name,
                    score=float(post.score),
                    published_at=datetime.fromtimestamp(post.created_utc, tz=timezone.utc),
                )
            )
    return candidates


def gather_candidates() -> list[Candidate]:
    """Pull hot + rising from every curated subreddit (used by the hourly job)."""
    reddit = _get_client()
    if reddit is None:
        return []
    all_subreddits = list_all_subreddits()
    candidates = _gather_from_subreddits(reddit, all_subreddits)
    log.info("Reddit: gathered %d candidates across %d subreddits", len(candidates), len(all_subreddits))
    return candidates


def gather_for_category(category: str) -> list[Candidate]:
    """Pull hot + rising from just one category's subreddits (used by the manual test-snapshot button)."""
    reddit = _get_client()
    if reddit is None:
        return []
    names = subreddits_for_category(category)
    candidates = _gather_from_subreddits(reddit, names)
    log.info("Reddit: gathered %d candidates for category=%s (%d subreddits)", len(candidates), category, len(names))
    return candidates
