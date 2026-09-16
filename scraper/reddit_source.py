"""
Reddit candidate source -- Bright Data's Reddit Scraper API.

Reddit's own API now requires manual Responsible Builder Policy approval
before you can even read public posts with an app-only OAuth token (the
old PRAW-based approach this module used to use), which is a hard
blocker for an unattended pipeline. Bright Data's Reddit Scraper API sits
in front of Reddit for us instead: we feed it one subreddit URL per
curated subreddit (scraper/subreddits.py is still the input list, same
as it fed PRAW before), it does the actual crawling/rendering on its
side, and hands back a batch of post records once the collection run
finishes.

One dataset-run call covers every subreddit in the list, so a whole pass
is one trigger + poll + fetch round trip via scraper/brightdata_client.py
rather than one PRAW listing call per subreddit. Bright Data's dataset
doesn't expose a separate "rising" listing the way PRAW did -- we pull
each subreddit's hot listing only; num_of_posts asks for roughly double
what we used to pull across hot+rising combined so coverage stays
comparable.

Bright Data's Reddit dataset's exact field names can vary by dataset
version, so record parsing below is deliberately tolerant: every field is
read through `_first()`, which tries several known/likely key spellings
and falls back gracefully rather than raising. If your dataset's schema
uses different keys, adjust the candidate lists passed to `_first()` --
nothing else needs to change.
"""
import logging
from datetime import datetime, timezone

import config
from scraper.brightdata_client import run_collection
from scraper.candidate import Candidate
from scraper.subreddits import category_for_subreddit, list_all_subreddits, subreddits_for_category

log = logging.getLogger("meme_pipeline.reddit")

VIDEO_DOMAINS = (
    "v.redd.it", "gfycat.com", "redgifs.com", "streamable.com",
    "youtube.com", "youtu.be", "clips.twitch.tv",
)
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _first(row: dict, *keys, default=None):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return default


def _to_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _first_image_url(row: dict):
    photos = _first(row, "photos", "images", "image_urls")
    if isinstance(photos, list) and photos:
        first = photos[0]
        if isinstance(first, dict):
            return first.get("url") or first.get("href")
        return first
    return _first(row, "image_url", "thumbnail")


def _first_list_url(row: dict, *keys):
    """Bright Data's newer Reddit shape returns media as a bare list of URL strings
    (row["videos"] / row["photos"]) instead of a single video_url/image_url field."""
    for key in keys:
        values = row.get(key)
        if isinstance(values, list) and values:
            first = values[0]
            if isinstance(first, dict):
                return first.get("url") or first.get("href")
            if first:
                return str(first)
    return None


def _crosspost_parent(row: dict) -> dict:
    """Crossposted rows carry Reddit's own crosspost_parent_list -- the wrapper
    (crossposting) row often has its own title/caption but no media of its own,
    since a crosspost doesn't re-host anything; the actual clip lives on the
    original post. Returns the parent dict if present, else {}."""
    parents = row.get("crosspost_parent_list")
    if isinstance(parents, list) and parents and isinstance(parents[0], dict):
        return parents[0]
    return {}


def _classify_row(row: dict, post_url: str):
    """Returns ('video'|'image'|'gif', media_url) or (None, None) for a post shape we don't handle.
    Falls back to the crosspost parent's media when the wrapper row itself has none."""
    if row.get("is_gallery"):
        return None, None  # multi-image galleries -- not handled, keep it simple

    parent = _crosspost_parent(row)
    video_list_url = _first_list_url(row, "videos") or _first_list_url(parent, "videos")
    video_url = _first(row, "video_url", "video") or video_list_url
    is_video = (bool(row.get("is_video")) or bool(parent.get("is_video")) or bool(video_url)
                or _first(row, "post_type") == "video")
    url = (post_url or "").lower()
    if is_video or any(domain in url for domain in VIDEO_DOMAINS) or url.endswith((".mp4", ".gifv", ".webm")):
        return "video", video_url or post_url
    if url.endswith(".gif"):
        return "gif", post_url
    image_url = (_first_image_url(row) or _first_list_url(row, "photos", "images")
                 or _first_image_url(parent) or _first_list_url(parent, "photos", "images"))
    if image_url or url.endswith(IMAGE_EXTS):
        return "image", image_url or post_url
    return None, None


def _duration_hint(row: dict):
    duration = _first(row, "video_duration", "duration")
    try:
        return float(duration) if duration is not None else None
    except (TypeError, ValueError):
        return None


def _uses_parent_media(row: dict, parent: dict) -> bool:
    """True if the wrapper row itself carries no media and we'd only get a
    video/image out of this post by falling back to the crosspost parent."""
    if not parent:
        return False
    own_video = _first(row, "video_url", "video") or _first_list_url(row, "videos")
    own_image = _first_image_url(row) or _first_list_url(row, "photos", "images")
    return not (own_video or own_image or row.get("is_video"))


def _candidate_from_row(row: dict, subreddit_name: str) -> Candidate | None:
    post_id = _first(row, "post_id", "id")
    if not post_id:
        return None
    # Bright Data's current Reddit shape doesn't echo a per-post permalink field
    # ("url"/"post_url"/"permalink" all come back as the subreddit's own URL or null),
    # so build the real post permalink ourselves from post_id + subreddit.
    short_id = str(post_id).split("_")[-1]
    permalink = f"https://www.reddit.com/r/{subreddit_name}/comments/{short_id}/"
    raw_link = _first(row, "url", "post_url", "permalink")
    if raw_link and str(raw_link).startswith("http") and "/comments/" in str(raw_link):
        permalink = str(raw_link)

    media_type, media_url = _classify_row(row, permalink)
    if media_type is None:
        return None

    if media_type == "video":
        duration = _duration_hint(row)
        if duration and not (config.MIN_CLIP_DURATION_SEC <= duration <= config.MAX_CLIP_DURATION_SEC):
            return None
        # v.redd.it serves video and audio as separate DASH streams. The direct
        # CDN url in row["videos"]/video_url points at the video-only track, so
        # downloading it straight gives a silent clip. yt-dlp's own reddit
        # extractor knows to fetch and mux the matching audio track, but only
        # when it's given the post permalink, not the raw fallback video url --
        # so for v.redd.it specifically we hand it the permalink instead.
        if media_url and "v.redd.it" in str(media_url).lower():
            media_url = permalink

    parent = _crosspost_parent(row)
    # If this post is a crosspost and the wrapper carries no media of its own,
    # the clip we're actually downloading is the ORIGINAL post's content, so
    # its title should describe that content -- not the crossposter's caption,
    # which is often unrelated commentary rather than a description of the clip.
    if _uses_parent_media(row, parent):
        title = _first(parent, "title", default="") or _first(row, "title", default="")
    else:
        title = _first(row, "title", default="")

    category = category_for_subreddit(subreddit_name)
    return Candidate(
        source="reddit",
        source_id=str(post_id),
        source_url=permalink,
        media_url=media_url or permalink,
        media_type=media_type,
        category=category,
        title=title,
        author=str(_first(row, "author", "username", default="")),
        subreddit=subreddit_name,
        score=_to_float(_first(row, "num_upvotes", "score", "upvotes", default=0)),
        published_at=_parse_datetime(_first(row, "date_posted", "created_time", "created_at")),
    )


def _gather_from_subreddits(subreddit_names: list[str]) -> list[Candidate]:
    if not subreddit_names:
        return []
    inputs = [
        {"url": f"https://www.reddit.com/r/{name}/"}
        for name in subreddit_names
    ]
    rows = run_collection(config.BRIGHTDATA_REDDIT_DATASET_ID, inputs)

    by_subreddit: dict[str, str] = {name.lower(): name for name in subreddit_names}
    candidates = []
    seen_ids: set[str] = set()
    for row in rows:
        subreddit_raw = _first(row, "community_name", "subreddit", "community")
        name = by_subreddit.get(str(subreddit_raw).lstrip("r/").lower()) if subreddit_raw else None
        if name is None:
            # Fall back to whichever subreddit this row's permalink names, if the
            # dataset didn't echo back a clean community_name field.
            permalink = str(_first(row, "url", "post_url", "permalink", default=""))
            parts = permalink.split("/r/", 1)
            guess = parts[1].split("/", 1)[0] if len(parts) > 1 else None
            name = by_subreddit.get((guess or "").lower(), guess)
        if not name:
            continue

        post_id = _first(row, "post_id", "id")
        if post_id in seen_ids:
            continue
        seen_ids.add(post_id)

        candidate = _candidate_from_row(row, name)
        if candidate:
            candidates.append(candidate)
    return candidates


def gather_candidates() -> list[Candidate]:
    """Pull hot posts from every curated subreddit in one Bright Data collection run (hourly job)."""
    all_subreddits = list_all_subreddits()
    candidates = _gather_from_subreddits(all_subreddits)
    log.info("Reddit: gathered %d candidates across %d subreddits", len(candidates), len(all_subreddits))
    return candidates


def gather_for_category(category: str) -> list[Candidate]:
    """Pull hot posts from just one category's subreddits (manual test-snapshot button)."""
    names = subreddits_for_category(category)
    candidates = _gather_from_subreddits(names)
    log.info("Reddit: gathered %d candidates for category=%s (%d subreddits)", len(candidates), category, len(names))
    return candidates
