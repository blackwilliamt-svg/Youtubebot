"""
Downloads a winning candidate (via yt-dlp, which handles reddit/youtube/
vimeo/gfycat/redgifs/streamable URLs uniformly), compresses it with ffmpeg
to keep disk usage sane on a 2GB droplet, generates a thumbnail, and drops
everything into /media/YYYY-MM-DD/<category>/ with a JSON provenance
sidecar.
"""
import json
import logging
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

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


def _probe_duration(path: Path) -> float:
    result = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _compress(src: Path, dst: Path):
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
    ts = max(duration / 2, 0.5)
    _run([
        "ffmpeg", "-y", "-ss", str(ts), "-i", str(video),
        "-frames:v", "1", "-q:v", "4", str(thumb),
    ])


def download_and_store(candidate: Candidate) -> dict:
    """
    Downloads + compresses `candidate`, writes it under MEDIA_ROOT, and
    returns the fields ready to hand to db.insert_clip(). Raises
    DownloadError on failure -- caller decides whether to try the
    next-best candidate.
    """
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dest_dir = config.MEDIA_ROOT / date_str / candidate.category
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{candidate.source}_{candidate.source_id}"
    final_video = dest_dir / f"{stem}.mp4"
    final_thumb = dest_dir / f"{stem}.jpg"
    sidecar = dest_dir / f"{stem}.json"

    with tempfile.TemporaryDirectory(prefix="meme_dl_") as tmp:
        tmp_dir = Path(tmp)
        raw_path = _ytdlp_download(candidate.media_url, tmp_dir)

        duration = _probe_duration(raw_path)
        if not (config.MIN_CLIP_DURATION_SEC <= duration <= config.MAX_CLIP_DURATION_SEC):
            raise DownloadError(
                f"duration {duration:.1f}s outside [{config.MIN_CLIP_DURATION_SEC}, "
                f"{config.MAX_CLIP_DURATION_SEC}]"
            )

        _compress(raw_path, final_video)
        _make_thumbnail(final_video, final_thumb, duration)

    retrieval_ts = datetime.now(timezone.utc).isoformat()
    sidecar.write_text(json.dumps({
        "source": candidate.source,
        "source_id": candidate.source_id,
        "source_url": candidate.source_url,
        "title": candidate.title,
        "author": candidate.author,
        "subreddit": candidate.subreddit,
        "category": candidate.category,
        "retrieved_at": retrieval_ts,
        "trending_score": candidate.trending_score,
    }, indent=2))

    log.info("Downloaded %s/%s -> %s", candidate.source, candidate.source_id, final_video)

    return {
        "source": candidate.source,
        "source_id": candidate.source_id,
        "source_url": candidate.source_url,
        "media_url": candidate.media_url,
        "subreddit": candidate.subreddit,
        "category": candidate.category,
        "title": candidate.title,
        "author": candidate.author,
        "score": candidate.score,
        "trending_score": candidate.trending_score,
        "duration_sec": _probe_duration(final_video),
        "fetched_at": retrieval_ts,
        "file_path": str(final_video),
        "thumb_path": str(final_thumb),
    }
