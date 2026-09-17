"""
Motion/scene-change-based frame sampling, replacing flat-rate sampling.

Walks a video (or gif -- OpenCV reads both through the same VideoCapture
API) at its native frame rate, keeping a new frame only once
FRAME_SAMPLE_REST_SEC has passed since the last *kept* frame -- unless
the last kept frame and the current candidate frame differ by more than
FRAME_DIFF_THRESHOLD (a simple mean-absolute-difference on downscaled
grayscale, a la basic OpenCV frame-differencing), in which case the
shorter FRAME_SAMPLE_MOTION_SEC interval applies instead. That gives
denser capture during motion/scene changes and sparser capture at rest,
without pulling in a heavier scene-detection dependency (PySceneDetect)
for what's fundamentally a cheap per-frame comparison.
"""
import logging
from pathlib import Path

import cv2

import config

log = logging.getLogger("meme_pipeline.analyzer.frames")

_DIFF_SIZE = (160, 90)  # downscaled before diffing -- plenty for a motion signal, cheap to compute


def _gray_signature(frame):
    small = cv2.resize(frame, _DIFF_SIZE)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)


def extract_frames(path: Path, max_frames: int = None) -> list[tuple[float, "cv2.Mat"]]:
    """Returns [(timestamp_sec, frame), ...], sampled per the motion-based
    schedule above, capped at max_frames (oldest-first, evenly spread
    across however much of the clip has already been walked -- clips are
    short (<=15s) so this rarely actually caps anything). Returns []
    on any read failure rather than raising."""
    max_frames = max_frames or config.ANALYZER_MAX_FRAMES
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        log.warning("Could not open %s for frame sampling", path)
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    if fps <= 0:
        fps = 24.0
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = (frame_count / fps) if frame_count > 0 else None
    step = 1.0 / fps

    kept: list[tuple[float, "cv2.Mat"]] = []
    last_kept_sig = None
    last_kept_t = None
    t = 0.0
    try:
        while len(kept) < max_frames:
            if duration is not None and t > duration + step:
                break
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                break

            sig = _gray_signature(frame)
            motion = False
            if last_kept_sig is not None:
                diff = cv2.absdiff(sig, last_kept_sig)
                motion = float(diff.mean()) > config.FRAME_DIFF_THRESHOLD

            interval = config.FRAME_SAMPLE_MOTION_SEC if motion else config.FRAME_SAMPLE_REST_SEC
            if last_kept_t is None or (t - last_kept_t) >= interval:
                kept.append((t, frame))
                last_kept_sig = sig
                last_kept_t = t

            t += max(step, 0.02)
    except Exception:
        log.exception("Frame sampling failed partway through %s; using %d frame(s) captured so far", path, len(kept))
    finally:
        cap.release()

    return kept


def extract_single_frame(path: Path) -> list[tuple[float, "cv2.Mat"]]:
    """For static images -- just the one frame, wrapped in the same
    [(t, frame), ...] shape the vision analyzer expects."""
    frame = cv2.imread(str(path))
    if frame is None:
        log.warning("Could not read image %s", path)
        return []
    return [(0.0, frame)]
