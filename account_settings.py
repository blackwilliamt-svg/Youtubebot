"""
Dashboard login credentials (DASHBOARD_USERNAME / DASHBOARD_PASSWORD_HASH),
editable from the dashboard itself (/account) instead of only via .env +
gen_password_hash.py.

Same DB-backed-override pattern as api_settings.py -- values live in the
`settings` table (db.py's get_all_settings/set_setting), applied onto the
`config` module's attributes at startup and immediately on save, so the
rest of the app (which reads config.DASHBOARD_USERNAME /
config.DASHBOARD_PASSWORD_HASH as plain attributes, e.g. app.py's /login)
picks up a change without a restart. Kept as its own small module rather
than folded into api_settings.API_FIELDS since those are third-party API
creds shown on /settings with different display rules (secret masking,
per-field help text) -- login credentials get their own page and their
own validation (current-password re-check, hashing) instead.
"""
import config
import db

ACCOUNT_KEYS = ("DASHBOARD_USERNAME", "DASHBOARD_PASSWORD_HASH")

# Snapshot of what config.py loaded from .env, taken at import time (before
# apply_overrides() runs) -- mirrors api_settings.ENV_DEFAULTS, so a DB
# override can never permanently hide whatever .env/the environment set.
ENV_DEFAULTS = {key: getattr(config, key, "") for key in ACCOUNT_KEYS}


def apply_overrides() -> None:
    """Push any DB-saved username/password-hash onto config.*. Call once at
    startup, after db.init_db() -- same spot api_settings.apply_overrides() runs."""
    stored = db.get_all_settings()
    for key in ACCOUNT_KEYS:
        value = stored.get(key)
        if value:
            setattr(config, key, value)


def save(key: str, value: str) -> None:
    if key not in ACCOUNT_KEYS:
        raise ValueError(f"Unknown account setting: {key}")
    db.set_setting(key, value)
    setattr(config, key, value)
