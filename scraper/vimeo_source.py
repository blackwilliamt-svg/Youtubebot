"""
Vimeo candidate source. Vimeo's catalog skews toward short films rather
than meme clips, so this is the smallest of the three sources -- one
search per hourly run, rotated by category like the YouTube search leg.
Needs a personal/app access token with the default public scope (no user
OAuth required for read-only public search).
"""
import logging
from datetime import datetime, timezone

import requests

import config
from scraper.candidate import Candidate
from scraper.retry import with_backoff

log = logging.getLogger("meme_pipeline.vimeo")

API_BASE = "https://api.vimeo.com"

SEARCH_QUERIES = [
    ("fail compilation", "fails"),
    ("satisfying", "oddly-satisfying"),
    ("funny animal", "animals"),
    ("gaming highlight", "gaming"),
    ("amazing moment", "wins"),
]

# Vimeo's `license` field values that count as Creative Commons.
CC_LICENSES = {"CC", "CC-BY", "CC-BY-NC", "CC-BY-NC-ND", "CC-BY-NC-SA", "CC-BY-ND", "CC-BY-SA", "CC0"}


class _RequestError(Exception):
    pass


@with_backoff(exceptions=(_RequestError,), max_attempts=4)
def _get(path, params):
    headers = {"Authorization": f"bearer {config.VIMEO_ACCESS_TOKEN}"}
    resp = requests.get(f"{API_BASE}{path}", params=params, headers=headers, timeout=15)
    if resp.status_code == 429 or resp.status_code >= 500:
        raise _RequestError(f"{resp.status_code}: {resp.text[:200]}")
    resp.raise_for_status()
    return resp.json()


def gather_candidates() -> list[Candidate]:
    if not config.VIMEO_ACCESS_TOKEN:
        log.warning("VIMEO_ACCESS_TOKEN not configured; skipping vimeo source")
        return []

    query, category = SEARCH_QUERIES[datetime.now(timezone.utc).hour % len(SEARCH_QUERIES)]
    try:
        data = _get(
            "/videos",
            {
                "query": query,
                "sort": "plays",
                "direction": "desc",
                "per_page": 15,
                "filter": "CC",
                "fields": "uri,name,link,duration,stats.plays,release_time,license,user.name",
            },
        )
    except (_RequestError, requests.RequestException) as exc:
        log.warning("Vimeo search failed for %r: %s", query, exc)
        return []

    candidates = []
    for item in data.get("data", []):
        license_ = (item.get("license") or "").upper()
        if license_ and license_ not in CC_LICENSES:
            continue
        duration = item.get("duration") or 0
        if not (config.MIN_CLIP_DURATION_SEC <= duration <= config.MAX_CLIP_DURATION_SEC):
            continue
        uri = item.get("uri", "")
        video_id = uri.rsplit("/", 1)[-1]
        if not video_id:
            continue
        published_at = None
        if item.get("release_time"):
            try:
                published_at = datetime.fromisoformat(item["release_time"].replace("Z", "+00:00"))
            except ValueError:
                pass
        candidates.append(
            Candidate(
                source="vimeo",
                source_id=video_id,
                source_url=item.get("link", f"https://vimeo.com/{video_id}"),
                media_url=item.get("link", f"https://vimeo.com/{video_id}"),
                category=category,
                title=item.get("name", ""),
                author=(item.get("user") or {}).get("name", ""),
                score=float((item.get("stats") or {}).get("plays") or 0),
                published_at=published_at,
            )
        )
    log.info("Vimeo: gathered %d candidates for query %r", len(candidates), query)
    return candidates
