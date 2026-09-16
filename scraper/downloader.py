"""
Downloads a winning candidate and drops it into
/media/YYYY-MM-DD/<category>/ with a JSON provenance sidecar. Three
paths depending on candidate.media_type:

  video  -- yt-dlp (handles reddit/youtube/vimeo/gfycat/redgifs/streamable
            uniformly), then ffmpeg H.264 compress + a jpg thumbnail.
  image  -- plain HTTP download, normalized to a size-capped jpg via ffmpeg.
  gif    -- plain HTTP download, kept as-is (re-encoding a gif is lossy and
            fiddly), plus a jpg thumbnail of its first frame.

compiler/build.py is what turns an image/gif into an actual video segment
at compilation time (see SLIDE_DURATION_SEC) -- this module just fetches
and stores the source media.
"""
import json
import logging
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests
import yt_dlp

import config
from scraper.candidate import Candidate

log = logging.getLogger("meme_pipeline.downloader")


class DownloadError(Exception):
    pass


def _run(cmd: list[str], **kwargs):
    log.debug("+ %s", " ".join(cmd))
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


def _ytdlp_download(url: str, dest_dir: Path) -> Path:
    ydl_opts = {
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "merge_output_format": "mp4",
        "outtmpl": str(dest_dir / "raw.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "max_filesize": 300 * 1024 * 1024,  # 300MB safety cap
        "socket_timeout": 30,
        "retries": 3,
    }
    if config.YTDLP_COOKIES_FILE:
        ydl_opts["cookiefile"] = config.YTDLP_COOKIES_FILE

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as exc:
        raise DownloadError(str(exc)) from exc

    matches = list(dest_dir.glob("raw.*"))
    if not matches:
        raise DownloadError(f"yt-dlp reported success but no output file found for {url}")
    return matches[0]


def _download_raw(url: str, dest: Path, max_bytes: int):
    try:
        with requests.get(url, stream=True, timeout=20, headers={"User-Agent": config.REDDIT_USER_AGENT}) as resp:
            resp.raise_for_status()
            total = 0
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    total += len(chunk)
                    if total > max_bytes:
                        raise DownloadError(f"{url} exceeded {max_bytes} byte cap")
                    f.write(chunk)
    except requests.RequestException as exc:
        raise DownloadError(f"failed to fetch {url}: {exc}") from exc
    if dest.stat().st_size == 0:
        raise DownloadError(f"{url} downloaded as an empty file")


def _probe_duration(path: Path) -> float:
    result = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _compress_video(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-i", str(src),
        "-c:v", "libx264", "-preset", config.FFMPEG_PRESET, "-crf", str(config.FFMPEG_CRF),
        "-vf", "scale='min(1080,iw)':'-2'",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-map_metadata", "-1",
        str(dst),
    ])


def _make_thumbnail(video: Path, thumb: Path, duration: float):
    thumb.parent.mkdir(parents=True, exist_ok=True)
    ts = max(duration / 2, 0.1)
    _run([
        "ffmpeg", "-y", "-ss", str(ts), "-i", str(video),
        "-frames:v", "1", "-q:v", "4", str(thumb),
    ])


def _normalize_image(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-i", str(src),
        "-vf", "scale='min(1920,iw)':'-2'",
        "-map_metadata", "-1", "-q:v", "3",
        str(dst),
    ])


def _guess_ext(url: str, content_type: str, fallback: str) -> str:
    path_ext = Path(urlsplit(url).path).suffix.lower()
    if path_ext:
        return path_ext
    if content_type:
        guess = "." + content_type.split("/")[-1].split(";")[0].strip()
        if len(guess) <= 6:
            return guess
    return fallback


def _store_video(candidate: Candidate, dest_dir: Path, stem: str) -> dict:
    final_video = dest_dir / f"{stem}.mp4"
    final_thumb = dest_dir / f"{stem}.jpg"
    with tempfile.TemporaryDirectory(prefix="meme_dl_") as tmp:
        tmp_dir = Path(tmp)
        raw_path = _ytdlp_download(candidate.media_url, tmp_dir)
        duration = _probe_duration(raw_path)
        if not (config.MIN_CLIP_DURATION_SEC <= duration <= config.MAX_CLIP_DURATION_SEC):
            raise DownloadError(
                f"duration {duration:.1f}s outside [{config.MIN_CLIP_DURATION_SEC}, "
                f"{config.MAX_CLIP_DURATION_SEC}]"
            )
        _compress_video(raw_path, final_video)
        _make_thumbnail(final_video, final_thumb, duration)
    return {"file_path": str(final_video), "thumb_path": str(final_thumb),
            "duration_sec": _probe_duration(final_video)}


def _store_image(candidate: Candidate, dest_dir: Path, stem: str) -> dict:
    final_image = dest_dir / f"{stem}.jpg"
    with tempfile.TemporaryDirectory(prefix="meme_dl_") as tmp:
        raw_path = Path(tmp) / ("raw" + _guess_ext(candidate.media_url, "", ".jpg"))
        _download_raw(candidate.media_url, raw_path, config.MAX_IMAGE_DOWNLOAD_BYTES)
        _normalize_image(raw_path, final_image)
    # the image itself doubles as its own thumbnail -- no separate file needed
    return {"file_path": str(final_image), "thumb_path": str(final_image), "duration_sec": None}


def _store_gif(candidate: Candidate, dest_dir: Path, stem: str) -> dict:
    final_gif = dest_dir / f"{stem}.gif"
    final_thumb = dest_dir / f"{stem}.jpg"
    _download_raw(candidate.media_url, final_gif, config.MAX_GIF_DOWNLOAD_BYTES)
    duration = _probe_duration(final_gif)
    _make_thumbnail(final_gif, final_thumb, duration)
    return {"file_path": str(final_gif), "thumb_path": str(final_thumb), "duration_sec": duration}


_STORERS = {"video": _store_video, "image": _store_image, "gif": _store_gif}


def download_and_store(candidate: Candidate) -> dict:
    """
    Downloads + stores `candidate` under MEDIA_ROOT, and returns the
    fields ready to hand to db.insert_clip(). Raises DownloadError on
    failure -- caller decides whether to try the next-best candidate.
    """
    storer = _STORERS.get(candidate.media_type)
    if storer is None:
        raise DownloadError(f"unknown media_type {candidate.media_type!r}")

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dest_dir = config.MEDIA_ROOT / date_str / candidate.category
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{candidate.source}_{candidate.source_id}"

    stored = storer(candidate, dest_dir, stem)

    retrieval_ts = datetime.now(timezone.utc).isoformat()
    sidecar = dest_dir / f"{stem}.json"
    sidecar.write_text(json.dumps({
        "source": candidate.source,
        "source_id": candidate.source_id,
        "source_url": candidate.source_url,
        "media_type": candidate.media_type,
        "title": candidate.title,
        "author": candidate.author,
        "subreddit": candidate.subreddit,
        "category": candidate.category,
        "retrieved_at": retrieval_ts,
        "trending_score": candidate.trending_score,
    }, indent=2))

    log.info("Downloaded %s/%s (%s) -> %s", candidate.source, candidate.source_id,
              candidate.media_type, stored["file_path"])

    return {
        "source": candidate.source,
        "source_id": candidate.source_id,
        "source_url": candidate.source_url,
        "media_url": candidate.media_url,
        "media_type": candidate.media_type,
        "subreddit": candidate.subreddit,
        "category": candidate.category,
        "title": candidate.title,
        "author": candidate.author,
        "score": candidate.score,
        "trending_score": candidate.trending_score,
        "fetched_at": retrieval_ts,
        **stored,
    }


# Re-exported (unprefixed) for other in-repo modules -- currently
# manual_upload.py -- that need the same compress/thumbnail/probe
# primitives without going through the yt-dlp/HTTP download paths above.
compress_video = _compress_video
make_thumbnail = _make_thumbnail
probe_duration = _probe_duration
