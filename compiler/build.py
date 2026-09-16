"""
Stitches a dashboard-assembled sequence of items into a single vertical
(9:16) YouTube Shorts-spec compilation: materialize any image/gif into a
short video slide, scale/pad every item to the same frame, crossfade
video+audio at each cut, layer a sound effect on top of every cut (from
library/sfx/ if it has something, else a synthesized fallback), duck in
background music under any segment with no native audio, and hard-cap
the whole thing under MAX_COMPILATION_SEC.

Entry point: build_compilation(items) -- run this on a background thread
from the Flask app (see app.py's /build route); it blocks on ffmpeg for
anywhere from ~10s to a couple of minutes depending on item count/length,
which is too slow for a request/response cycle.

`items` is a list of already-resolved dicts (see app.py's
_resolve_sequence), one per sequence position, in stitch order:
    {"ref": "clip:15" | "reaction:fails/boing.mp4", "id": int|None,
     "media_type": "video"|"image"|"gif", "category": str, "file_path": str}
"""
import json
import logging
import subprocess
import tempfile
from pathlib import Path

import config
import db
import library
from compiler import sfx_gen
from lockutil import pipeline_lock

log = logging.getLogger("meme_pipeline.build")

MAX_PER_CLIP_SEC = 12.0
MIN_CLIP_SEC = 2.0
SOFT_WARNING_ITEM_COUNT = 10  # matches the dashboard's own soft warning, kept here just for logging
MAX_ITEM_VOLUME = 3.0  # dashboard's per-item volume slider caps here too

# ffmpeg xfade filter's built-in transition names -- picked from the full
# list (https://ffmpeg.org/ffmpeg-filters.html#xfade) for ones that read
# clearly at 9:16/vertical-Shorts scale. Exposed as a dropdown on /build;
# an unrecognized value (shouldn't happen from the dashboard's own <select>,
# but a build() caller could pass anything) falls back to "fade" rather
# than failing the whole compilation over it.
ALLOWED_TRANSITIONS = (
    "fade", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown",
    "circlecrop", "dissolve", "pixelize",
)
DEFAULT_TRANSITION = "fade"


class BuildError(Exception):
    pass


def _run(cmd: list[str]):
    log.debug("+ %s", " ".join(cmd))
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def _probe(path) -> dict:
    result = _run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    return json.loads(result.stdout)


def _duration_and_audio(path) -> tuple[float, bool]:
    info = _probe(path)
    duration = float(info.get("format", {}).get("duration", 0.0))
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    return duration, has_audio


def _slide_vf() -> str:
    W, H = config.OUTPUT_WIDTH, config.OUTPUT_HEIGHT
    return (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30")


def _materialize_slide(item: dict, tmp_dir: Path) -> Path:
    """Video items pass through untouched; image/gif items get rendered to a short silent mp4 slide."""
    kind = item.get("media_type", "video")
    if kind == "video":
        return Path(item["file_path"])

    safe_name = item["ref"].replace(":", "_").replace("/", "_").replace("\\", "_")
    dest = tmp_dir / f"slide_{safe_name}.mp4"
    if kind == "image":
        _run([
            "ffmpeg", "-y", "-loop", "1", "-i", item["file_path"],
            "-t", str(config.SLIDE_DURATION_SEC),
            "-vf", _slide_vf(), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest),
        ])
    elif kind == "gif":
        _run([
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", item["file_path"],
            "-t", str(config.SLIDE_DURATION_SEC),
            "-vf", _slide_vf(), "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest),
        ])
    else:
        raise BuildError(f"unknown media_type {kind!r} for {item.get('ref')}")
    return dest


def _compute_trims(durations: list[float]) -> list[float]:
    T = config.TRANSITION_SEC
    n = len(durations)
    overlap_total = T * max(n - 1, 0)
    target_sum = config.MAX_COMPILATION_SEC + overlap_total

    # With few items selected, let each run longer than the usual per-clip
    # cap rather than always chopping to MAX_PER_CLIP_SEC (a single
    # selected item should get close to the full budget, not 12s of it).
    per_clip_cap = max(MAX_PER_CLIP_SEC, target_sum / n)
    capped = [min(d, per_clip_cap) for d in durations]
    total = sum(capped)
    if total > target_sum and total > 0:
        scale = target_sum / total
        capped = [max(MIN_CLIP_SEC, d * scale) for d in capped]
    return capped


def _filter_graph(n: int, trims: list[float], has_audio: list[bool],
                   sfx_files: list[Path], music_choice: list,
                   trim_starts: list[float], volumes: list[float],
                   transition: str = DEFAULT_TRANSITION):
    """
    Returns (filter_complex_str, video_out_label, audio_out_label, music_paths_in_input_order).
    ffmpeg input index layout: [0..n-1] = items, [n..n+n_cuts-1] = one sfx
    file per cut, [n+n_cuts..] = one music file per silent segment that
    got one (music_choice[i] is None for segments with native audio or an
    empty music library).

    trim_starts[i]: seconds to skip from the start of item i before taking
    its trims[i]-second slice -- lets a clip's best part be used instead of
    always whatever happens to be first. volumes[i]: multiplier (0=mute,
    1=unchanged, up to MAX_ITEM_VOLUME) applied to item i's own native
    audio; has no effect on a silent item routed to background music.
    """
    T = config.TRANSITION_SEC
    if transition not in ALLOWED_TRANSITIONS:
        log.warning("Unknown transition %r requested, falling back to %r", transition, DEFAULT_TRANSITION)
        transition = DEFAULT_TRANSITION
    W, H = config.OUTPUT_WIDTH, config.OUTPUT_HEIGHT
    n_cuts = max(n - 1, 0)
    parts = []

    # Assign ffmpeg input indices to the music tracks we're actually using.
    music_input_index = {}
    ordered_music_paths = []
    next_idx = n + n_cuts
    for i in range(n):
        if music_choice[i] is not None:
            music_input_index[i] = next_idx
            ordered_music_paths.append(music_choice[i])
            next_idx += 1

    # Per-item scale/pad/trim -> v{i}, and trim/format audio -> a{i}
    # (native audio, ducked background music for a silent segment, or
    # plain silence if there's no music library at all).
    for i in range(n):
        parts.append(
            f"[{i}:v]trim=start={trim_starts[i]:.3f}:duration={trims[i]:.3f},setpts=PTS-STARTPTS,"
            f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30[v{i}]"
        )
        if has_audio[i]:
            parts.append(
                f"[{i}:a]atrim=start={trim_starts[i]:.3f}:duration={trims[i]:.3f},asetpts=PTS-STARTPTS,"
                f"volume={volumes[i]:.3f},"
                f"aformat=sample_rates=44100:channel_layouts=stereo[a{i}]"
            )
        elif i in music_input_index:
            midx = music_input_index[i]
            parts.append(
                f"[{midx}:a]atrim=duration={trims[i]:.3f},asetpts=PTS-STARTPTS,"
                f"volume={config.MUSIC_DUCK_VOLUME},"
                f"aformat=sample_rates=44100:channel_layouts=stereo[a{i}]"
            )
        else:
            parts.append(
                f"anullsrc=r=44100:cl=stereo,atrim=duration={trims[i]:.3f}[a{i}]"
            )

    if n == 1:
        return ";".join(parts), "v0", "a0", ordered_music_paths

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
            f"[{cur_v}][v{i}]xfade=transition={transition}:duration={T:.3f}:offset={offset:.3f}[{out_v}]"
        )
        parts.append(
            f"[{cur_a}][a{i}]acrossfade=d={T:.3f}:c1=tri:c2=tri[{out_a}]"
        )
        cum_duration = cum_duration + trims[i] - T
        cur_v, cur_a = out_v, out_a

    # Layer this cut's sfx: delay it to land exactly on the cut, then amix
    # everything with the crossfaded track. normalize=0 keeps the main
    # audio at full volume instead of amix's default 1/N attenuation;
    # alimiter catches any peak where a sfx hit lands on an already-loud
    # moment.
    sfx_labels = []
    for k, offset in enumerate(cut_offsets):
        src_idx = n + k
        delay_ms = max(int(offset * 1000), 0)
        lbl = f"sfxd{k}"
        parts.append(
            f"[{src_idx}:a]volume=0.55,adelay={delay_ms}|{delay_ms}[{lbl}]"
        )
        sfx_labels.append(f"[{lbl}]")

    mix_inputs = f"[{cur_a}]" + "".join(sfx_labels)
    parts.append(
        f"{mix_inputs}amix=inputs={len(sfx_labels) + 1}:duration=first:"
        f"dropout_transition=0:normalize=0,alimiter=limit=0.95[aout]"
    )

    return ";".join(parts), cur_v, "aout", ordered_music_paths


def build_compilation(items: list[dict], compilation_id: int | None = None,
                       transition: str = DEFAULT_TRANSITION) -> dict:
    """
    Builds the compilation and updates the `compilations` row. If
    compilation_id is None, a new row is created first. Returns the
    updated compilation dict. Raises BuildError on failure (the row is
    still updated with status='failed' + error for the dashboard to show).

    Each item dict may carry "trim_start" (seconds to skip before taking
    its slice -- clamped so at least MIN_CLIP_SEC of the source remains;
    ignored/forced to 0 for image/gif slides, which have no natural start
    point) and "volume" (0..MAX_ITEM_VOLUME multiplier on that item's own
    native audio, default 1.0). Both are optional -- missing means
    trim-from-start at normal volume, the original behavior.
    """
    if not items:
        raise BuildError("No items in the sequence")
    for it in items:
        if not it.get("file_path") or not Path(it["file_path"]).exists():
            raise BuildError(f"{it.get('ref', 'item')} is missing its media file on disk")
    if len(items) > SOFT_WARNING_ITEM_COUNT:
        log.info("Building a %d-item compilation (soft warning threshold is %d) -- may feel rushed",
                  len(items), SOFT_WARNING_ITEM_COUNT)

    if compilation_id is None:
        compilation_id = db.create_compilation([it["ref"] for it in items])

    with pipeline_lock() as acquired:
        if not acquired:
            db.update_compilation(compilation_id, status="failed",
                                   error="Pipeline busy (scrape or another build in progress); try again shortly.")
            raise BuildError("pipeline lock held by another job")

        try:
            with tempfile.TemporaryDirectory(prefix="meme_build_") as tmp:
                tmp_dir = Path(tmp)
                resolved_paths = [_materialize_slide(it, tmp_dir) for it in items]

                raw_durations, has_audio = zip(*(_duration_and_audio(p) for p in resolved_paths))
                n = len(items)

                # Clamp each item's requested start-trim so at least
                # MIN_CLIP_SEC of it remains; slides (image/gif) always
                # start at 0 -- there's no "later part" of a static slide.
                trim_starts = []
                available = []
                for i in range(n):
                    is_video = items[i].get("media_type", "video") == "video"
                    requested = float(items[i].get("trim_start") or 0.0) if is_video else 0.0
                    ts = max(0.0, min(requested, max(raw_durations[i] - MIN_CLIP_SEC, 0.0)))
                    trim_starts.append(ts)
                    available.append(raw_durations[i] - ts)

                trims = _compute_trims(available)
                volumes = []
                for i in range(n):
                    raw_volume = items[i].get("volume")
                    v = 1.0 if raw_volume is None else float(raw_volume)
                    volumes.append(max(0.0, min(v, MAX_ITEM_VOLUME)))

                # One sfx per cut, hinted by the category of the incoming item.
                chosen_sfx = [sfx_gen.pick_sfx(items[i]["category"]) for i in range(1, n)]

                # One (looped) background music track per silent segment, if the library has any.
                music_choice = [
                    library.pick_library_music() if not has_audio[i] else None
                    for i in range(n)
                ]

                filter_complex, v_label, a_label, music_paths = _filter_graph(
                    n, trims, list(has_audio), chosen_sfx, music_choice,
                    trim_starts, volumes, transition=transition,
                )

                out_dir = config.MEDIA_ROOT / "compilations"
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / f"compilation_{compilation_id}.mp4"

                cmd = ["ffmpeg", "-y"]
                for p in resolved_paths:
                    cmd += ["-i", str(p)]
                for sfx in chosen_sfx:
                    cmd += ["-i", str(sfx)]
                for music in music_paths:
                    cmd += ["-stream_loop", "-1", "-i", str(music)]
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

                final_duration, _ = _duration_and_audio(out_path)

            db.update_compilation(
                compilation_id, status="ready", output_path=str(out_path),
                duration_sec=final_duration, error=None,
            )
            real_ids = [it["id"] for it in items if it.get("id") is not None]
            db.set_clips_compilation(real_ids, compilation_id)
            log.info("Compilation %d ready: %s (%.1fs, %d items)", compilation_id, out_path, final_duration, n)
            return db.get_compilation(compilation_id)
        except subprocess.CalledProcessError as exc:
            err = exc.stderr[-2000:] if exc.stderr else str(exc)
            log.error("ffmpeg failed for compilation %d: %s", compilation_id, err)
            db.update_compilation(compilation_id, status="failed", error=err)
            raise BuildError(err) from exc
        except BuildError as exc:
            db.update_compilation(compilation_id, status="failed", error=str(exc))
            raise
