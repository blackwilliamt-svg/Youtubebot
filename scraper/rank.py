"""
Picks the single "most trending" candidate out of everything gathered in a
pass, across all three platforms. Raw engagement numbers aren't comparable
across platforms (Reddit upvotes vs YouTube views vs Vimeo plays), so we
rank by *velocity* -- engagement per hour since posting -- which rewards
things that are trending right now over old high-water-mark posts, and
happens to put all three sources on roughly the same footing.

Video and image/gif candidates are ranked as two entirely separate pools
(different scales, different competitors) -- callers are responsible for
splitting by `media_type` before calling into this module; nothing here
ever compares a video's velocity to an image's.
"""
import json
import logging

import db
from scraper.candidate import Candidate
from scraper.downloader import DownloadError, download_and_store

log = logging.getLogger("meme_pipeline.rank")


def _analyze_and_annotate(fields: dict) -> dict:
    """Runs the freeform vision+audio analyzer (analyzer/) on a just-
    downloaded clip and folds tags/caption/transcript into the fields dict
    that gets handed to db.insert_clip(). Best-effort: analyzer failures
    are logged and swallowed so a slow/unreachable local model never takes
    down the scrape."""
    try:
        from analyzer.pipeline import analyze_clip
        analysis = analyze_clip(fields["file_path"], fields["media_type"])
        fields["tags"] = json.dumps(analysis.get("tags", []))
        fields["transcript"] = analysis.get("transcript", "")
    except Exception:
        log.exception("Analyzer failed for %s; storing clip without tags", fields.get("file_path"))
        fields.setdefault("tags", json.dumps([]))
        fields.setdefault("transcript", "")
    return fields


def score_candidate(c: Candidate) -> float:
    c.trending_score = c.score / c.hours_since_published()
    return c.trending_score


def rank_fresh(candidates: list[Candidate]) -> list[Candidate]:
    """Dedups against the rolling 24h window, scores, and returns highest-velocity-first."""
    fresh = [c for c in candidates if not db.is_duplicate(c.source, c.source_id)]
    dropped = len(candidates) - len(fresh)
    if dropped:
        log.info("Rank: dropped %d already-seen candidates (24h dedup)", dropped)
    for c in fresh:
        score_candidate(c)
    fresh.sort(key=lambda c: c.trending_score, reverse=True)
    return fresh


def pick_winner(candidates: list[Candidate]) -> Candidate | None:
    fresh = rank_fresh(candidates)
    if not fresh:
        return None
    winner = fresh[0]
    log.info(
        "Rank: winner=%s/%s score=%.2f (%d candidates considered)",
        winner.source, winner.source_id, winner.trending_score, len(fresh),
    )
    return winner


def pull_top_candidate(candidates: list[Candidate], label: str = "") -> int | None:
    """
    Ranks `candidates` (already restricted to one media-type bucket by the
    caller), then downloads the best one -- falling through to the next-best
    up to 5 deep if a download fails (dead link, geo-block, etc.). Records a
    scrape_runs row either way. Returns the new clip id, or None if nothing
    qualified / everything failed.
    """
    ranked = rank_fresh(candidates)
    if not ranked:
        db.log_scrape_run(len(candidates), None, f"no qualifying candidate ({label})" if label else "no qualifying candidate")
        log.info("No qualifying candidate this pass%s.", f" ({label})" if label else "")
        return None

    for attempt, candidate in enumerate(ranked[:5], start=1):
        try:
            fields = download_and_store(candidate)
        except DownloadError as exc:
            log.warning("Attempt %d (%s): %s/%s failed to download (%s); trying next candidate",
                        attempt, label, candidate.source, candidate.source_id, exc)
            continue
        except Exception:
            log.exception("Attempt %d (%s): unexpected error downloading %s/%s; trying next candidate",
                          attempt, label, candidate.source, candidate.source_id)
            continue

        fields = _analyze_and_annotate(fields)
        clip_id = db.insert_clip(**fields)
        try:
            from scraper.engagement import compute_and_store
            compute_and_store(clip_id)
        except Exception:
            log.exception("Engagement scoring failed for clip id=%d", clip_id)
        db.log_scrape_run(len(candidates), clip_id, label)
        log.info("Stored clip id=%d (%s/%s, category=%s, media_type=%s)%s", clip_id, candidate.source,
                  candidate.source_id, candidate.category, candidate.media_type,
                  f" [{label}]" if label else "")
        return clip_id

    db.log_scrape_run(len(candidates), None, f"top candidates all failed to download ({label})")
    log.error("All top candidates failed to download this pass (%s).", label)
    return None
