"""
Orchestrates the analyzer package end to end for one downloaded clip:
sample frames (motion-based for video, single-frame for image/gif),
transcribe speech (video only), then call the vision model with both.
Called from scraper/rank.py right after a clip is downloaded and stored,
before it's inserted into the DB, so tags/caption/transcript can be
saved on the clips row from the start.
"""
import logging
from pathlib import Path

from analyzer.audio import transcribe
from analyzer.frame_sampler import extract_frames, extract_single_frame
from analyzer.vision import analyze as vision_analyze

log = logging.getLogger("meme_pipeline.analyzer.pipeline")


def analyze_clip(file_path: str, media_type: str) -> dict:
    """Returns {"tags": [...], "caption": str, "transcript": str}. Never
    raises -- every stage degrades gracefully (see the individual
    modules), so a clip with no usable audio/frames just comes back with
    empty tags rather than blocking ingestion."""
    path = Path(file_path)
    transcript = ""
    frames = []

    try:
        if media_type == "video":
            frames = extract_frames(path)
            transcript = transcribe(path)
        else:  # image / gif -- no speech, and "motion" sampling degrades to a still frame / a few gif frames
            frames = extract_frames(path) if media_type == "gif" else extract_single_frame(path)
    except Exception:
        log.exception("Frame/audio extraction failed for %s (media_type=%s)", path, media_type)

    if not frames:
        return {"tags": [], "caption": "", "transcript": transcript}

    result = vision_analyze(frames, transcript=transcript)
    return {"tags": result.get("tags", []), "caption": result.get("caption", ""), "transcript": transcript}
