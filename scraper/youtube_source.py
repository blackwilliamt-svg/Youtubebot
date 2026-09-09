"""
YouTube candidate source.

Two cheap strategies combined each hour, chosen to stay well inside the
default 10,000 units/day Data API quota:

1. `videos.list(chart=mostPopular)` per a couple of relevant category ids.
   This is a flat 1 unit per call *no matter how many parts you request*,
   so we ask for snippet+contentDetails+statistics+status in one shot and
   post-filter to license == 'creativeCommon'.
2. One `search.list(videoLicense=creativeCommon, order=viewCount,
   publishedAfter=<last 26h>)` call per run, using a query term that
   rotates by hour of day so all five categories get covered over a day.
   search.list is expensive (100 units/call) -- this is deliberately
   capped at exactly one call per hourly run (~2,400 units/day).
"""
import logging
from datetime import datetime, timedelta, timezone

import requests

import config
from scraper.candidate import Candidate
from scraper.retry import with_backoff

log = logging.getLogger("meme_pipeline.youtube")

API_BASE = "https://www.googleapis.com/youtube/v3"

TRENDING_CATEGORY_MAP = {
    "15": "animals",       # Pets & Animals
    "20": "gaming",        # Gaming
    "17": "wins",          # Sports
    "23": "fails",         # Comedy
    "24": "oddly-satisfying",  # Entertainment
}

# (search query, category) -- one is used per hourly run, rotated by hour.
SEARCH_QUERIES = [
    ("epic fail compilation clip", "fails"),
    ("oddly satisfying clip", "oddly-satisfying"),
    ("amazing animal moment clip", "animals"),
    ("insane gaming clip", "gaming"),
    ("wholesome win moment clip", "wins"),
]


class _RequestError(Exception):
    pass


@with_backoff(exceptions=(_RequestError,), max_attempts=4)
def _get(path, params):
    params = {**params, "key": config.YOUTUBE_API_KEY}
    resp = requests.get(f"{API_BASE}/{path}", params=params, timeout=15)
    if resp.status_code == 429 or resp.status_code >= 500:
        raise _RequestError(f"{resp.status_code}: {resp.text[:200]}")
    resp.raise_for_status()
    return resp.json()


def _duration_from_iso8601(dur: str) -> float:
    """Cheap ISO-8601 'PT#M#S' duration parser -- avoids pulling in isodate."""
    import re

    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", dur or "")
    if not m:
        return 0.0
    days, hours, minutes, seconds = (int(g) if g else 0 for g in m.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _candidate_from_item(item, category) -> Candidate | None:
    vid = item.get("id")
    if isinstance(vid, dict):
        vid = vid.get("videoId")
    if not vid:
        return None
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    content = item.get("contentDetails", {})
    duration = _duration_from_iso8601(content.get("duration", ""))
    if content and not (config.MIN_CLIP_DURATION_SEC <= duration <= config.MAX_CLIP_DURATION_SEC):
        return None
    published_at = None
    if snippet.get("publishedAt"):
        published_at = datetime.fromisoformat(snippet["publishedAt"].replace("Z", "+00:00"))
    return Candidate(
        source="youtube",
        source_id=vid,
        source_url=f"https://www.youtube.com/watch?v={vid}",
        media_url=f"https://www.youtube.com/watch?v={vid}",
        category=category,
        title=snippet.get("title", ""),
        author=snippet.get("channelTitle", ""),
        score=float(stats.get("viewCount", 0) or 0),
        published_at=published_at,
    )


def _gather_trending() -> list[Candidate]:
    candidates = []
    for cat_id, category in TRENDING_CATEGORY_MAP.items():
        try:
            data = _get(
                "videos",
                {
                    "part": "snippet,contentDetails,statistics,status",
                    "chart": "mostPopular",
                    "regionCode": "US",
                    "videoCategoryId": cat_id,
                    "maxResults": 15,
                },
            )
        except (_RequestError, requests.RequestException) as exc:
            log.warning("YouTube trending fetch failed for category %s: %s", cat_id, exc)
            continue
        for item in data.get("items", []):
            if item.get("status", {}).get("license") != "creativeCommon":
                continue
            cand = _candidate_from_item(item, category)
            if cand:
                candidates.append(cand)
    return candidates


def _gather_search() -> list[Candidate]:
    query, category = SEARCH_QUERIES[datetime.now(timezone.utc).hour % len(SEARCH_QUERIES)]
    published_after = (datetime.now(timezone.utc) - timedelta(hours=26)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        data = _get(
            "search",
            {
                "part": "snippet",
                "type": "video",
                "videoLicense": "creativeCommon",
                "order": "viewCount",
                "publishedAfter": published_after,
                "q": query,
                "maxResults": 10,
                "safeSearch": "moderate",
            },
        )
    except (_RequestError, requests.RequestException) as exc:
        log.warning("YouTube search fetch failed for %r: %s", query, exc)
        return []

    ids = [it["id"]["videoId"] for it in data.get("items", []) if it.get("id", {}).get("videoId")]
    if not ids:
        return []

    # One cheap videos.list call to get duration + stats for the search hits.
    try:
        details = _get(
            "videos",
            {"part": "snippet,contentDetails,statistics", "id": ",".join(ids)},
        )
    except (_RequestError, requests.RequestException) as exc:
        log.warning("YouTube video details fetch failed: %s", exc)
        return []

    return [c for c in (_candidate_from_item(item, category) for item in details.get("items", [])) if c]


def gather_candidates() -> list[Candidate]:
    if not config.YOUTUBE_API_KEY:
        log.warning("YOUTUBE_API_KEY not configured; skipping youtube source")
        return []
    candidates = _gather_trending() + _gather_search()
    log.info("YouTube: gathered %d candidates", len(candidates))
    return candidates
