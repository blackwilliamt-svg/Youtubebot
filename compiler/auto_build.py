"""
Automated (no manual sequencing) compilation builder -- picks clips by
highest composite engagement score (scraper/engagement.py) until total
runtime lands in [config.AUTO_COMPILATION_MIN_SEC, ...MAX_SEC] (60-90s),
then stitches them with STRAIGHT CUTS ONLY: no transitions, sfx, or
background music, unlike the manual /build screen in compiler/build.py,
which still supports all of that for hand-assembled compilations. That
manual screen is untouched by this module -- they're two independent
ways to produce a `compilations` row.

Selected clips are marked status='used' (via db.set_clips_compilation)
but never deleted, so they stay available if a future longer-form
(5-10 min, ~2x/week) compilation wants to reuse them alongside that
period's near-misses -- a planned future feature this data model is
deliberately kept flexible enough for, per the spec.

Entry point: build_auto_compilation() -- run this from a background
thread/process (see scraper/run_autobuild.py), not a request/response
cycle; it blocks on ffmpeg the same way compiler.build.build_compilation
does.
"""
import logging
import subprocess
import tempfile
from pathlib import Path

import config
import db
from lockutil import pipeline_lock

log = logging.getLogger("meme_pipeline.auto_build")


class AutoBuildError(Exception):
    pass


def _run(cmd: list[str]):
    log.debug("+ %s", " ".join(cmd))
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def _duration(path: Path) -> float:
    result = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def select_clips_for_compilation() -> list[dict]:
    """Greedy pick, highest engagement_score first, from triaged/unused
    clips with a known duration, until cumulative duration lands inside
    the target window. Returns [] if the pool can't reach the 60s floor
    (not enough material yet -- try again next pass, nothing is
    consumed)."""
    pool = db.get_available_clips_for_autobuild()
    chosen: list[dict] = []
    total = 0.0
    for clip in pool:
        dur = clip.get("duration_sec") or (config.SLIDE_DURATION_SEC if clip["media_type"] != "video" else 0.0)
        if dur <= 0:
            continue
        # A single clip longer than the whole target window can never fit --
        # skip it outright rather than letting it through as the first pick.
        # (New clips are capped at MAX_CLIP_DURATION_SEC, but a DB that
        # predates that cap being lowered can still hold long ones.)
        if total + dur > config.AUTO_COMPILATION_MAX_SEC:
            continue  # skip this one, keep looking for something that still fits
        chosen.append(clip)
        total += dur
        if total >= config.AUTO_COMPILATION_MIN_SEC:
            break
    if total < config.AUTO_COMPILATION_MIN_SEC:
        return []
    return chosen


def _slide_vf() -> str:
    W, H = config.OUTPUT_WIDTH, config.OUTPUT_HEIGHT
    return (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30")


def _materialize(clip: dict, tmp_dir: Path) -> Path:
    """Video passes through untouched (gets normalized in the concat step
    below anyway); image/gif gets rendered to a short silent mp4 slide,
    same as the manual builder's approach."""
    kind = clip.get("media_type", "video")
    if kind == "video":
        return Path(clip["file_path"])

    dest = tmp_dir / f"slide_{clip['id']}.mp4"
    if kind == "image":
        _run([
            "ffmpeg", "-y", "-loop", "1", "-i", clip["file_path"],
            "-t", str(config.SLIDE_DURATION_SEC),
            "-vf", _slide_vf(), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest),
        ])
    elif kind == "gif":
        _run([
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", clip["file_path"],
            "-t", str(config.SLIDE_DURATION_SEC),
            "-vf", _slide_vf(), "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest),
        ])
    else:
        raise AutoBuildError(f"unknown media_type {kind!r} for clip id={clip.get('id')}")
    return dest


def build_auto_compilation() -> dict | None:
    """Builds a straight-cut compilation from the current top composite-
    engagement clips. Returns the finished compilation dict, or None if
    there wasn't enough qualifying material to reach the 60s floor (no
    `compilations` row is created in that case)."""
    clips = select_clips_for_compilation()
    if not clips:
        log.info("Auto-build: not enough qualifying material for a 60-90s compilation this pass.")
        return None

    compilation_id = db.create_compilation([f"clip:{c['id']}" for c in clips])
    with pipeline_lock() as acquired:
        if not acquired:
            db.update_compilation(compilation_id, status="failed",
                                   error="Pipeline busy (scrape or another build in progress); try again shortly.")
            return None

        try:
            with tempfile.TemporaryDirectory(prefix="meme_autobuild_") as tmp:
                tmp_dir = Path(tmp)
                resolved = [_materialize(c, tmp_dir) for c in clips]

                # Normalize every input to the same frame size/format/audio
                # layout, then concat -- straight cuts, no xfade/sfx/music.
                norm_paths = []
                for i, p in enumerate(resolved):
                    norm = tmp_dir / f"norm_{i}.mp4"
                    _run([
                        "ffmpeg", "-y", "-i", str(p),
                        "-vf", _slide_vf(),
                        "-c:v", "libx264", "-preset", config.FFMPEG_PRESET, "-crf", str(config.FFMPEG_CRF),
                        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
                        "-movflags", "+faststart", str(norm),
                    ])
                    norm_paths.append(norm)

                concat_list = tmp_dir / "concat.txt"
                concat_list.write_text("".join(f"file '{p.as_posix()}'\n" for p in norm_paths))

                out_dir = config.MEDIA_ROOT / "compilations"
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / f"compilation_{compilation_id}.mp4"
                _run([
                    "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
                    "-c", "copy", str(out_path),
                ])

                final_duration = _duration(out_path)

            db.update_compilation(
                compilation_id, status="ready", output_path=str(out_path),
                duration_sec=final_duration, error=None,
            )
            db.set_clips_compilation([c["id"] for c in clips], compilation_id)
            log.info("Auto-build: compilation %d ready (%.1fs, %d clips, straight cuts)",
                      compilation_id, final_duration, len(clips))
            return db.get_compilation(compilation_id)
        except subprocess.CalledProcessError as exc:
            err = exc.stderr[-2000:] if exc.stderr else str(exc)
            log.error("ffmpeg failed for auto-build compilation %d: %s", compilation_id, err)
            db.update_compilation(compilation_id, status="failed", error=err)
            raise AutoBuildError(err) from exc
        except AutoBuildError as exc:
            db.update_compilation(compilation_id, status="failed", error=str(exc))
            raise
