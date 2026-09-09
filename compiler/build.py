"""
Stitches the dashboard's selected clips into a single vertical (9:16)
YouTube Shorts-spec compilation: scale/pad every clip to the same frame,
crossfade video+audio at each cut, layer a synthesized sound effect on
top of every cut, and hard-cap the whole thing under MAX_COMPILATION_SEC.

Entry point: build_compilation(clip_ids) -- run this on a background
thread from the Flask app (see app.py's /build route); it blocks on
ffmpeg for anywhere from ~10s to a couple of minutes depending on clip
count/length, which is too slow for a request/response cycle.
"""
import json
import logging
import random
import subprocess
from pathlib import Path

import config
import db
from compiler.sfx_gen import list_sfx
from lockutil import pipeline_lock

log = logging.getLogger("meme_pipeline.build")

MAX_PER_CLIP_SEC = 12.0
MIN_CLIP_SEC = 2.0


class BuildError(Exception):
    pass


def _run(cmd: list[str]):
    log.debug("+ %s", " ".join(cmd))
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def _probe(path: str) -> dict:
    result = _run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ])
    return json.loads(result.stdout)


def _duration_and_audio(path: str) -> tuple[float, bool]:
    info = _probe(path)
    duration = float(info.get("format", {}).get("duration", 0.0))
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    return duration, has_audio


def _compute_trims(durations: list[float]) -> list[float]:
    T = config.TRANSITION_SEC
    n = len(durations)
    overlap_total = T * max(n - 1, 0)
    target_sum = config.MAX_COMPILATION_SEC + overlap_total

    # With few clips selected, let each run longer than the usual per-clip
    # cap rather than always chopping to MAX_PER_CLIP_SEC (a single
    # selected clip should get close to the full budget, not 12s of it).
    per_clip_cap = max(MAX_PER_CLIP_SEC, target_sum / n)
    capped = [min(d, per_clip_cap) for d in durations]
    total = sum(capped)
    if total > target_sum and total > 0:
        scale = target_sum / total
        capped = [max(MIN_CLIP_SEC, d * scale) for d in capped]
    return capped


def _filter_graph(n: int, trims: list[float], has_audio: list[bool], sfx_count: int):
    """
    Returns (filter_complex_str, video_out_label, audio_out_label).
    Input index layout: [0..n-1] = clips, [n..n+sfx_count-1] = sfx files.
    """
    T = config.TRANSITION_SEC
    W, H = config.OUTPUT_WIDTH, config.OUTPUT_HEIGHT
    parts = []

    # Per-clip scale/pad/trim -> v{i}, and trim/format audio (or synthesize
    # silence for clips with no audio track) -> a{i}.
    for i in range(n):
        parts.append(
            f"[{i}:v]trim=duration={trims[i]:.3f},setpts=PTS-STARTPTS,"
            f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30[v{i}]"
        )
        if has_audio[i]:
            parts.append(
                f"[{i}:a]atrim=duration={trims[i]:.3f},asetpts=PTS-STARTPTS,"
                f"aformat=sample_rates=44100:channel_layouts=stereo[a{i}]"
            )
        else:
            parts.append(
                f"anullsrc=r=44100:cl=stereo,atrim=duration={trims[i]:.3f}[a{i}]"
            )

    # Chain crossfades. Track cumulative length of the joined-so-far stream.
    cur_v = "v0"
    cur_a = "a0"
    cum_duration = trims[0]
    cut_offsets = []  # timestamp (in the final timeline) of each cut, for sfx placement
    for i in range(1, n):
        offset = max(cum_duration - T, 0.0)
        cut_offsets.append(offset)
        out_v = f"vx{i}"
        out_a = f"ax{i}"
        parts.append(
            f"[{cur_v}][v{i}]xfade=transition=fade:duration={T:.3f}:offset={offset:.3f}[{out_v}]"
        )
        parts.append(
            f"[{cur_a}][a{i}]acrossfade=d={T:.3f}:c1=tri:c2=tri[{out_a}]"
        )
        cum_duration = cum_duration + trims[i] - T
        cur_v, cur_a = out_v, out_a

    if n == 1:
        return ";".join(parts), "v0", "a0"

    # Layer a random sfx on top of every cut: delay each sfx input to land
    # exactly on its cut, then amix everything with the crossfaded track.
    sfx_labels = []
    for k, offset in enumerate(cut_offsets):
        src_idx = n + (k % sfx_count)
        delay_ms = max(int(offset * 1000), 0)
        lbl = f"sfxd{k}"
        parts.append(
            f"[{src_idx}:a]volume=0.55,adelay={delay_ms}|{delay_ms}[{lbl}]"
        )
        sfx_labels.append(f"[{lbl}]")

    # normalize=0 keeps the main audio at full volume instead of amix's
    # default 1/N attenuation; alimiter catches any peak where a sfx hit
    # happens to land on top of an already-loud moment in the clip.
    mix_inputs = f"[{cur_a}]" + "".join(sfx_labels)
    parts.append(
        f"{mix_inputs}amix=inputs={len(sfx_labels) + 1}:duration=first:"
        f"dropout_transition=0:normalize=0,alimiter=limit=0.95[aout]"
    )

    return ";".join(parts), cur_v, "aout"


def build_compilation(clip_ids: list[int], compilation_id: int | None = None) -> dict:
    """
    Builds the compilation and updates the `compilations` row. If
    compilation_id is None, a new row is created first. Returns the
    updated compilation dict. Raises BuildError on failure (the row is
    still updated with status='failed' + error for the dashboard to show).
    """
    clips = db.get_clips_by_ids(clip_ids)
    if not clips:
        raise BuildError("No clips found for the given ids")
    for c in clips:
        if not c.get("file_path") or not Path(c["file_path"]).exists():
            raise BuildError(f"clip {c['id']} is missing its media file on disk")

    if compilation_id is None:
        compilation_id = db.create_compilation([c["id"] for c in clips])

    with pipeline_lock() as acquired:
        if not acquired:
            db.update_compilation(compilation_id, status="failed",
                                   error="Pipeline busy (scrape or another build in progress); try again shortly.")
            raise BuildError("pipeline lock held by another job")

        try:
            durations, has_audio = zip(*(_duration_and_audio(c["file_path"]) for c in clips))
            trims = _compute_trims(list(durations))

            sfx_files = list_sfx()
            if not sfx_files:
                raise BuildError("no sfx files available (compiler.sfx_gen.generate_all failed?)")
            n = len(clips)
            sfx_count = min(len(sfx_files), max(n - 1, 1))
            chosen_sfx = random.sample(sfx_files, sfx_count) if sfx_count <= len(sfx_files) else sfx_files

            filter_complex, v_label, a_label = _filter_graph(n, trims, list(has_audio), len(chosen_sfx))

            out_dir = config.MEDIA_ROOT / "compilations"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"compilation_{compilation_id}.mp4"

            cmd = ["ffmpeg", "-y"]
            for c in clips:
                cmd += ["-i", c["file_path"]]
            for sfx in chosen_sfx:
                cmd += ["-i", str(sfx)]
            cmd += [
                "-filter_complex", filter_complex,
                "-map", f"[{v_label}]", "-map", f"[{a_label}]",
                "-t", str(config.MAX_COMPILATION_SEC),
                "-c:v", "libx264", "-preset", config.FFMPEG_PRESET, "-crf", str(config.FFMPEG_CRF),
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                str(out_path),
            ]
            _run(cmd)

            final_duration, _ = _duration_and_audio(str(out_path))
            db.update_compilation(
                compilation_id, status="ready", output_path=str(out_path),
                duration_sec=final_duration, error=None,
            )
            db.set_clips_compilation([c["id"] for c in clips], compilation_id)
            log.info("Compilation %d ready: %s (%.1fs)", compilation_id, out_path, final_duration)
            return db.get_compilation(compilation_id)
        except subprocess.CalledProcessError as exc:
            err = exc.stderr[-2000:] if exc.stderr else str(exc)
            log.error("ffmpeg failed for compilation %d: %s", compilation_id, err)
            db.update_compilation(compilation_id, status="failed", error=err)
            raise BuildError(err) from exc
        except BuildError as exc:
            db.update_compilation(compilation_id, status="failed", error=str(exc))
            raise
