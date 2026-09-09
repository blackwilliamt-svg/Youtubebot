"""
Manual "paste any video URL" import -- additive and isolated from the
automated scrape/rank/dedup pipeline in scraper/. Nothing here is called
by run_hourly.py or scraper/snapshot.py, and nothing in scraper/ imports
this module; the only coupling is one-directional reuse of
scraper.downloader.download_and_store() so a manually-imported clip lands
in exactly the same media/YYYY-MM-DD/<category>/ layout, with the same
compression/thumbnail/JSON-sidecar treatment and the same 24h dedup log,
as anything the scrapers pulled -- from there on, /triage, /review, the
compiler and scraper/retention.py all treat it identically to a scraped
clip; they have no idea (and don't need to) that it arrived this way.

    from manual_import import import_url, ManualImportError
    clip_id = import_url("https://...", category="fails")

Two yt-dlp passes happen for one import: a metadata-only probe here (so a
bad URL, an unsupported site, or an out-of-range duration fails fast with
a clear message before anything is downloaded), then download_and_store()
does its own real download. That's deliberate -- it keeps this module
read-only with respect to scraper/downloader.py rather than reaching in to
share its internals.
"""
import hashlib
import logging

import yt_dlp

import config
import db
from scraper.candidate import Candidate
from scraper.downloader import DownloadError, download_and_store
from scraper.subreddits import CATEGORIES

log = logging.getLogger("meme_pipeline.manual_import")


class ManualImportError(Exception):
    """Anything about a manual import that should show the user a clear,
    specific error rather than a stack trace or a wedged pipeline."""


def _probe(url: str) -> dict:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
    }
    if config.YTDLP_COOKIES_FILE:
        ydl_opts["cookiefile"] = config.YTDLP_COOKIES_FILE

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        # The normal "this site/URL isn't supported, or extraction failed"
        # case -- yt-dlp's own message is already specific and useful.
        raise ManualImportError(f"yt-dlp couldn't get a video from that URL: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: never crash the request over this
        raise ManualImportError(f"Unexpected error reading that URL: {exc}") from exc

    if not info:
        raise ManualImportError("yt-dlp returned nothing for that URL.")
    if info.get("_type") == "playlist" or info.get("entries"):
        raise ManualImportError(
            "That URL points to a playlist/collection, not a single video -- "
            "paste a direct link to one video instead."
        )
    return info


def import_url(url: str, category: str) -> int:
    """
    Probes, downloads, compresses and stores one manually-pasted URL as a
    new clip -- triaged=0, exactly like a freshly-scraped clip, so it shows
    up in /triage next. Returns the new clip's id.

    Raises ManualImportError, with a message safe to show directly to the
    user, on: an empty/missing URL or category, a site yt-dlp can't
    extract from, a playlist URL, a duration outside the configured
    MIN/MAX_CLIP_DURATION_SEC bounds, a duplicate of something imported or
    scraped in the last 24h, or the download itself failing. Every failure
    path is also logged (log.warning) with the URL, per "log the failed
    URL" -- callers don't need to log it again.
    """
    url = (url or "").strip()
    if not url:
        raise ManualImportError("No URL given.")
    if category not in CATEGORIES:
        raise ManualImportError(f"Unknown category {category!r}.")

    try:
        info = _probe(url)

        source_id = str(info.get("id") or "").strip()
        if not source_id:
            # Rare (most extractors report one), but not fatal -- a stable
            # hash of the URL keeps the 24h dedup log working regardless.
            source_id = "url" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]

        if db.is_duplicate("manual", source_id):
            raise ManualImportError("This exact video was already imported in the last 24h.")

        duration = info.get("duration")
        if duration and not (config.MIN_CLIP_DURATION_SEC <= duration <= config.MAX_CLIP_DURATION_SEC):
            raise ManualImportError(
                f"That video is {duration:.0f}s long, outside the configured "
                f"[{config.MIN_CLIP_DURATION_SEC}, {config.MAX_CLIP_DURATION_SEC}]s range."
            )

        candidate = Candidate(
            source="manual",
            source_id=source_id,
            source_url=url,
            media_url=url,
            media_type="video",
            category=category,
            title=info.get("title") or "",
            author=info.get("uploader") or info.get("channel") or info.get("extractor_key") or "",
        )

        try:
            fields = download_and_store(candidate)
        except DownloadError as exc:
            raise ManualImportError(f"Download failed: {exc}") from exc

        clip_id = db.insert_clip(**fields)
        log.info("Manual import stored clip id=%d from %s", clip_id, url)
        return clip_id

    except ManualImportError as exc:
        log.warning("Manual import failed for %s: %s", url, exc)
        raise
