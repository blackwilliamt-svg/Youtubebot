"""
Shared client for Bright Data's Dataset (scraper) APIs -- used by
reddit_source.py, tiktok_source.py and instagram_source.py instead of each
source's old bespoke API integration.

All three "Scraper API" products (Reddit, YouTube, Vimeo) front the same
trigger/poll/fetch dataset flow: you POST a batch of input records (e.g.
{"url": "..."} or {"keyword": "..."}) against a dataset id, get back a
snapshot id, poll it until Bright Data finishes running the collector,
then fetch the finished rows as JSON. One trigger call per source per
scrape covers every subreddit/category/query for that source in a single
round trip, rather than one call per item.

Auth is a single bearer token (BRIGHTDATA_API_KEY) shared across every
dataset -- only the dataset id (and the input shape) differs per source.
"""
import logging
import time

import requests

import config
from scraper.retry import with_backoff

log = logging.getLogger("meme_pipeline.brightdata")

API_BASE = "https://api.brightdata.com/datasets/v3"


class BrightDataError(Exception):
    pass


class BrightDataTimeout(BrightDataError):
    pass


def _headers():
    return {
        "Authorization": f"Bearer {config.BRIGHTDATA_API_KEY}",
        "Content-Type": "application/json",
    }


@with_backoff(exceptions=(BrightDataError, requests.RequestException), max_attempts=4)
def _request(method, path, **kwargs):
    resp = requests.request(method, f"{API_BASE}{path}", headers=_headers(), timeout=30, **kwargs)
    if resp.status_code == 429 or resp.status_code >= 500:
        raise BrightDataError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
    resp.raise_for_status()
    return resp


def trigger_collection(dataset_id: str, inputs: list[dict], extra_params: dict | None = None) -> str:
    """Kicks off a collection run for `inputs` against `dataset_id`. Returns the snapshot id."""
    params = {"dataset_id": dataset_id, "include_errors": "true"}
    if extra_params:
        params.update(extra_params)
    resp = _request("POST", "/trigger", params=params, json=inputs)
    data = resp.json()
    snapshot_id = data.get("snapshot_id") or data.get("id")
    if not snapshot_id:
        raise BrightDataError(f"Trigger response had no snapshot_id: {data!r}")
    return snapshot_id


def _snapshot_status(snapshot_id: str) -> str:
    resp = _request("GET", f"/progress/{snapshot_id}")
    data = resp.json()
    return (data.get("status") or "").lower()


def _snapshot_data(snapshot_id: str) -> list[dict]:
    resp = _request("GET", f"/snapshot/{snapshot_id}", params={"format": "json"})
    data = resp.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("data") or data.get("results") or []
    return []


def wait_for_snapshot(snapshot_id: str, poll_interval: float = None, timeout: float = None) -> list[dict]:
    """Polls a snapshot until it's ready (or failed/timed out), then returns its rows."""
    poll_interval = poll_interval or config.BRIGHTDATA_POLL_INTERVAL_SEC
    timeout = timeout or config.BRIGHTDATA_POLL_TIMEOUT_SEC
    deadline = time.monotonic() + timeout
    while True:
        status = _snapshot_status(snapshot_id)
        if status in ("ready", "done", "completed", "success"):
            return _snapshot_data(snapshot_id)
        if status in ("failed", "error", "canceled", "cancelled"):
            raise BrightDataError(f"Snapshot {snapshot_id} finished with status={status!r}")
        if time.monotonic() >= deadline:
            raise BrightDataTimeout(f"Snapshot {snapshot_id} still {status!r} after {timeout:.0f}s")
        time.sleep(poll_interval)


def run_collection(dataset_id: str, inputs: list[dict], extra_params: dict | None = None) -> list[dict]:
    """Trigger + poll + fetch in one call. Returns [] (with a warning logged) on any failure
    rather than raising, so one flaky source never takes down the rest of an hourly run."""
    if not inputs:
        return []
    if not config.BRIGHTDATA_API_KEY:
        log.warning("BRIGHTDATA_API_KEY not configured; skipping dataset %s", dataset_id)
        return []
    if not dataset_id:
        log.warning("No dataset id configured for this source; skipping")
        return []
    try:
        snapshot_id = trigger_collection(dataset_id, inputs, extra_params)
        rows = wait_for_snapshot(snapshot_id)
        log.info("Bright Data dataset=%s: %d input(s) -> %d row(s)", dataset_id, len(inputs), len(rows))
        return rows
    except (BrightDataError, requests.RequestException) as exc:
        log.warning("Bright Data collection failed for dataset=%s: %s", dataset_id, exc)
        return []
