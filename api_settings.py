"""
Dashboard-editable API credentials (the /settings page).

Every scraper/upload module reads these off the `config` module as a plain
attribute lookup at call time (`config.REDDIT_CLIENT_ID`, etc.), not a copied
local, so overwriting `config.<KEY>` here takes effect immediately -- no
restart needed. Saved overrides live in the `settings` DB table; an absent
key just means "use whatever .env set" (captured in ENV_DEFAULTS below,
before any override is applied).
"""
import config
import db

API_FIELDS = [
    {
        "key": "REDDIT_CLIENT_ID",
        "label": "Client ID",
        "group": "Reddit",
        "secret": False,
        "help": "reddit.com/prefs/apps -> create app -> type \"script\".",
    },
    {
        "key": "REDDIT_CLIENT_SECRET",
        "label": "Client Secret",
        "group": "Reddit",
        "secret": True,
        "help": "From the same reddit.com/prefs/apps app.",
    },
    {
        "key": "REDDIT_USER_AGENT",
        "label": "User Agent",
        "group": "Reddit",
        "secret": False,
        "help": "Reddit requires a descriptive, unique user agent string.",
    },
    {
        "key": "YOUTUBE_API_KEY",
        "label": "API Key",
        "group": "YouTube Data API",
        "secret": True,
        "help": "Google Cloud Console -> Credentials -> API key, restricted to \"YouTube Data API v3\".",
    },
    {
        "key": "YT_OAUTH_CLIENT_ID",
        "label": "OAuth Client ID",
        "group": "YouTube OAuth (uploads)",
        "secret": False,
        "help": "Same Google Cloud project -> OAuth client ID, type \"Web application\".",
    },
    {
        "key": "YT_OAUTH_CLIENT_SECRET",
        "label": "OAuth Client Secret",
        "group": "YouTube OAuth (uploads)",
        "secret": True,
        "help": "From the same OAuth client.",
    },
    {
        "key": "YT_OAUTH_REDIRECT_URI",
        "label": "OAuth Redirect URI",
        "group": "YouTube OAuth (uploads)",
        "secret": False,
        "help": "Must be added as an authorized redirect URI on the OAuth client.",
    },
    {
        "key": "VIMEO_ACCESS_TOKEN",
        "label": "Access Token",
        "group": "Vimeo",
        "secret": True,
        "help": "developer.vimeo.com/apps -> generate a token with the default \"public\" scope.",
    },
]

# Snapshot of what config.py loaded from .env, taken at import time (before
# apply_overrides() runs) so "Reset to .env default" has something to fall
# back to even after we've overwritten the attribute.
ENV_DEFAULTS = {field["key"]: getattr(config, field["key"], "") for field in API_FIELDS}

_FIELDS_BY_KEY = {field["key"]: field for field in API_FIELDS}


def apply_overrides() -> None:
    """Push any DB-saved values onto config.*. Call once at startup, after db.init_db()."""
    stored = db.get_all_settings()
    for key, value in stored.items():
        if key in _FIELDS_BY_KEY and value:
            setattr(config, key, value)


def save(key: str, value: str) -> None:
    if key not in _FIELDS_BY_KEY:
        raise ValueError(f"Unknown setting: {key}")
    db.set_setting(key, value)
    setattr(config, key, value)


def reset(key: str) -> None:
    """Drop the DB override and fall back to the .env-derived default."""
    if key not in _FIELDS_BY_KEY:
        raise ValueError(f"Unknown setting: {key}")
    db.delete_setting(key)
    setattr(config, key, ENV_DEFAULTS.get(key, ""))


def grouped_fields() -> dict:
    """{group: [field + is_set + display_value, ...]} for rendering /settings.

    Secret fields never send their current value back to the browser --
    display_value is always blank for them, with is_set/placeholder text
    telling the user whether one is already configured.
    """
    groups: dict = {}
    for field in API_FIELDS:
        value = getattr(config, field["key"], "") or ""
        row = {
            **field,
            "is_set": bool(value),
            "display_value": "" if field["secret"] else value,
        }
        groups.setdefault(field["group"], []).append(row)
    return groups
