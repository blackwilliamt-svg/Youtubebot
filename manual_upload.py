"""
Manual "upload your own footage" import -- lets you drop your own clips
(personal footage, b-roll, anything not pulled from a scraper) straight
into the pipeline from a file picker on the dashboard.

Distinct from manual_import.py (which resolves a pasted URL via yt-dlp):
this one starts from a file already sitting on disk (the dashboard saves
the upload to a temp path before handing off to this module), so there's
no probing step -- just the same compress/thumbnail/store treatment as
the tail end of scraper/downloader.py's video path, reused via its
compress_video/make_thumbnail/probe_duration re-exports.

    from manual_upload import import_file, ManualUploadError
    clip_id = import_file(Path("/tmp/upload123.mp4"), "myclip.mov", category="funny-viral")

Lands as triaged=1 (skips /triage) -- it's footage you chose and uploaded
on purpose, so there's no need to re-approve it; it shows up directly in
/review, ready to add to a build. The caller is responsible for deleting
the temp source file afterwards either way (success or error).
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import config
import db
from scraper.downloader import compress_video, make_thumbnail, probe_duration
from scraper.subreddits import CATEGORIES

log = logging.getLogger("meme_pipeline.manual_upload")

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}


class ManualUploadError(Exception):
    """Anything about an uploaded file that should show the user a clear,
    specific error rather than a stack trace or a wedged pipeline."""


def import_file(tmp_path: Path, original_filename: str, category: str, title: str = "") -> int:
    """
    Compresses, thumbnails and stores an already-on-disk video file as a
    new clip, triaged=1 (goes straight to /review). Returns the new
    clip's id. Raises ManualUploadError, with a message safe to show
    directly to the user, on a bad category, an unsupported extension, or
    a file ffmpeg/ffprobe can't make sense of.
    """
    if category not in CATEGORIES:
        raise ManualUploadError(f"Unknown category {category!r}.")

    ext = Path(original_filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ManualUploadError(
            f"Unsupported file type {ext or '(none)'} -- try "
            f"{', '.join(sorted(e.lstrip('.') for e in ALLOWED_EXTENSIONS))}."
        )

    duration = probe_duration(tmp_path)
    if duration <= 0:
        raise ManualUploadError(
            "Couldn't read that as a video (ffprobe found no duration) -- "
            "is it a valid, non-empty video file?"
        )

    source_id = uuid.uuid4().hex
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dest_dir = config.MEDIA_ROOT / date_str / category
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = f"upload_{source_id}"
    final_video = dest_dir / f"{stem}.mp4"
    final_thumb = dest_dir / f"{stem}.jpg"

    try:
        compress_video(tmp_path, final_video)
        make_thumbnail(final_video, final_thumb, duration)
    except Exception as exc:  # noqa: BLE001 -- ffmpeg failures vary; surface them plainly
        raise ManualUploadError(f"ffmpeg couldn't process that file: {exc}") from exc

    final_duration = probe_duration(final_video)
    retrieval_ts = datetime.now(timezone.utc).isoformat()
    display_title = title.strip() if title and title.strip() else Path(original_filename).stem

    sidecar = dest_dir / f"{stem}.json"
    sidecar.write_text(json.dumps({
        "source": "upload",
        "source_id": source_id,
        "source_url": "",
        "media_type": "video",
        "title": display_title,
        "author": "",
        "subreddit": None,
        "category": category,
        "retrieved_at": retrieval_ts,
        "trending_score": 0.0,
        "original_filename": original_filename,
    }, indent=2))

    clip_id = db.insert_clip(
        source="upload",
        source_id=source_id,
        source_url="",
        media_url="",
        media_type="video",
        subreddit=None,
        category=category,
        title=display_title,
        author="",
        score=0.0,
        trending_score=0.0,
        fetched_at=retrieval_ts,
        file_path=str(final_video),
        thumb_path=str(final_thumb),
        duration_sec=final_duration,
        triaged=1,
    )
    log.info("Manual upload stored clip id=%d from %s (category=%s)", clip_id, original_filename, category)
    return clip_id
