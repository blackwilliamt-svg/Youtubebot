"""
Speech transcription layer (added alongside the vision-only frame
analysis) -- Whisper via faster-whisper, small/medium model, CPU-only
int8 inference (fine alongside the vision model on droplet-class
hardware; no GPU assumed). Model is loaded lazily and cached at module
level so repeated calls in the same process don't reload it every clip.
"""
import logging
from pathlib import Path

import config

log = logging.getLogger("meme_pipeline.analyzer.audio")

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(config.WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    return _model


def transcribe(path: Path) -> str:
    """Best-effort speech transcript for `path`. Returns "" (never raises)
    if Whisper isn't installed/available, the clip has no speech, or
    transcription fails for any other reason -- an untranscribed clip
    still gets vision-only tags."""
    try:
        model = _get_model()
        segments, _info = model.transcribe(str(path), beam_size=1, vad_filter=True)
        text = " ".join(seg.text.strip() for seg in segments if seg.text and seg.text.strip())
        return text.strip()
    except ImportError:
        log.warning("faster-whisper not installed; skipping transcription (pip install faster-whisper)")
        return ""
    except Exception:
        log.exception("Transcription failed for %s", path)
        return ""
