"""
Clip-selection "trust dial" -- how much autonomy the pipeline has to pick
clips into a compilation on its own, without you assembling one by hand
from /build.

    manual      -- (default) you build every compilation yourself from /build.
    assisted    -- the pipeline tells you (in the scrape/autobuild logs)
                   once enough top-ranked material exists for a 60-90s
                   compilation, but still waits for you to build it.
    autonomous  -- compiler.auto_build actually builds (never uploads) one
                   automatically once enough material exists.

Persisted in the `settings` table (same mechanism as api_settings.py's
API-credential overrides) so it survives restarts and is editable from
the dashboard's /settings page without a redeploy. Increase/decrease
this over time as build quality does -- see scraper/run_autobuild.py,
the entrypoint that actually reads this before doing anything.

Thumbnail selection is meant to reuse this exact same manual/assisted/
autonomous pattern later (manual first, bot-suggested later, per the
spec) -- that's not built yet, just noted here so the pattern doesn't
have to be reinvented when it is.
"""
import db
import config

SETTING_KEY = "clip_autonomy_level"


def get_level() -> str:
    return db.get_setting_value(SETTING_KEY) or config.DEFAULT_AUTONOMY_LEVEL


def set_level(level: str) -> None:
    if level not in config.AUTONOMY_LEVELS:
        raise ValueError(f"Unknown autonomy level {level!r} (must be one of {config.AUTONOMY_LEVELS}).")
    db.set_setting(SETTING_KEY, level)
