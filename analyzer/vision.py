"""
Local vision-language model call (Ollama, config.OLLAMA_VISION_MODEL --
e.g. "llava:7b") -- looks at the sampled frames (and, if given, the
speech transcript) and generates freeform tags for the clip. There is no
fixed tag list: the model is prompted to produce whatever tags it thinks
apply, guided toward the pipeline's actual content focus (funny/viral
memes, fails, political satire specifically as *satire*, not
reaction-to-news commentary).

Requires an Ollama server reachable at config.OLLAMA_HOST with
OLLAMA_VISION_MODEL already pulled (`ollama pull llava:7b`) -- see
README for droplet setup. CPU-only inference is slow (multi-second to
tens-of-seconds per clip on a small droplet); this runs once per
downloaded clip, not in the request/response path, so that's acceptable
but worth knowing if the hourly job starts backing up.
"""
import base64
import json
import logging

import requests

import config

log = logging.getLogger("meme_pipeline.analyzer.vision")

PROMPT = """You are tagging a short vertical video clip for a meme/fail/political-satire compilation channel.

Look at the sampled frames below, given in chronological order, and (if provided) the speech transcript. \
The channel's focus is funny/viral meme content, fails, and political satire/meme content specifically -- \
satirical or joke political content, NOT straight reaction-to-news-moments commentary. Animal/cute content \
and gaming content are de-emphasized; still tag them if that's genuinely what the clip is, just don't force \
a fit.

Generate whatever short (1-3 word) freeform tags you think best describe this clip -- there is no fixed \
list to choose from. Include a tag for the general kind of content (e.g. "fail", "funny-viral", \
"political-satire") as well as more specific descriptive tags (e.g. "skateboard fail", "cat", "jd vance \
impression", "road rage").

Respond with ONLY a JSON object, no other text, in this exact shape:
{"tags": ["tag-one", "tag-two", ...], "caption": "one sentence describing what happens in the clip"}"""


def _encode_frame(frame) -> str:
    ok, buf = cv2_imencode(frame)
    if not ok:
        raise ValueError("failed to JPEG-encode a sampled frame")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def cv2_imencode(frame):
    import cv2  # local import -- keeps this module importable even if opencv isn't installed
    return cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])


def analyze(frames: list[tuple[float, "object"]], transcript: str = "") -> dict:
    """frames: [(timestamp_sec, frame), ...] from analyzer.frame_sampler.
    Returns {"tags": [...], "caption": str}. Never raises -- any failure
    (Ollama unreachable, model not pulled, malformed response) yields
    {"tags": [], "caption": ""} so clip ingestion never blocks on this."""
    if not frames:
        return {"tags": [], "caption": ""}

    try:
        images = [_encode_frame(f) for _, f in frames]
    except Exception:
        log.exception("Failed to encode sampled frames for the vision model")
        return {"tags": [], "caption": ""}

    prompt = PROMPT
    if transcript:
        prompt += f"\n\nSpeech transcript: \"{transcript[:2000]}\""

    try:
        resp = requests.post(
            f"{config.OLLAMA_HOST.rstrip('/')}/api/generate",
            json={
                "model": config.OLLAMA_VISION_MODEL,
                "prompt": prompt,
                "images": images,
                "stream": False,
                "format": "json",
            },
            timeout=config.ANALYZER_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "{}")
        data = json.loads(raw)
    except requests.RequestException:
        log.warning("Could not reach the local vision model at %s (Ollama not running / not pulled?)",
                    config.OLLAMA_HOST)
        return {"tags": [], "caption": ""}
    except (ValueError, json.JSONDecodeError):
        log.warning("Vision model returned non-JSON output; skipping tags for this clip")
        return {"tags": [], "caption": ""}
    except Exception:
        log.exception("Unexpected error calling the vision model")
        return {"tags": [], "caption": ""}

    tags = []
    for t in data.get("tags", []) if isinstance(data, dict) else []:
        tag = str(t).strip().lower()
        if tag and tag not in tags:
            tags.append(tag)
    caption = str(data.get("caption", "")).strip() if isinstance(data, dict) else ""
    return {"tags": tags[: config.ANALYZER_MAX_TAGS], "caption": caption}
