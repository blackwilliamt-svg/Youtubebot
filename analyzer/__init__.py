"""
Freeform vision+audio clip analyzer.

    analyzer.pipeline.analyze_clip(file_path, media_type) -> {"tags": [...], "caption": str, "transcript": str}

Replaces the old fixed-bucket classification (there wasn't a fixed
classification model in this repo before this pass -- this whole package
is new) with an open tagging system: a local vision-language model
(analyzer/vision.py, via Ollama) looks at motion/scene-change-sampled
frames (analyzer/frame_sampler.py) plus a Whisper speech transcript
(analyzer/audio.py, video only) and generates whatever tags it thinks
are relevant. Those tags feed both display/classification (the /triage
and /review pages) and the adaptive search-parameter system
(scraper/adaptive.py) -- the same tag pool drives both.

Everything here is best-effort: any failure (Ollama not reachable, a
corrupt frame, an unreadable audio track) is caught and logged, and
analyze_clip() degrades to fewer tags/an empty transcript rather than
failing clip ingestion -- an untagged clip is still a perfectly usable
clip, just one the adaptive system can't learn from yet.
"""
