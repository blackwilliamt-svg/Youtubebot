"""
YouTube OAuth (installed-app style, but served through the Flask
dashboard so it works over the SSH tunnel with no browser running on the
droplet). Flow:

  1. Dashboard shows "Connect YouTube" -> GET /youtube/authorize
  2. That redirects to Google's consent screen
  3. Google redirects back to YT_OAUTH_REDIRECT_URI (which the SSH tunnel
     forwards to the droplet's Flask app) -> GET /youtube/oauth2callback
  4. Tokens are saved to config.YT_TOKEN_PATH (chmod 600, outside the repo)

Register YT_OAUTH_REDIRECT_URI (default http://localhost:8080/youtube/oauth2callback)
as an authorized redirect URI on the OAuth client in Google Cloud Console.
"""
import json
import logging
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

import config

log = logging.getLogger("meme_pipeline.youtube_oauth")

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _client_config():
    return {
        "web": {
            "client_id": config.YT_OAUTH_CLIENT_ID,
            "client_secret": config.YT_OAUTH_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [config.YT_OAUTH_REDIRECT_URI],
        }
    }


def is_configured() -> bool:
    return bool(config.YT_OAUTH_CLIENT_ID and config.YT_OAUTH_CLIENT_SECRET)


def build_flow() -> Flow:
    return Flow.from_client_config(
        _client_config(), scopes=SCOPES, redirect_uri=config.YT_OAUTH_REDIRECT_URI
    )


def get_authorization_url():
    flow = build_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    return auth_url, state


def exchange_code(code: str) -> Credentials:
    flow = build_flow()
    flow.fetch_token(code=code)
    creds = flow.credentials
    save_credentials(creds)
    return creds


def save_credentials(creds: Credentials):
    config.YT_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.YT_TOKEN_PATH.write_text(creds.to_json())
    os.chmod(config.YT_TOKEN_PATH, 0o600)


def load_credentials() -> Credentials | None:
    if not config.YT_TOKEN_PATH.exists():
        return None
    data = json.loads(config.YT_TOKEN_PATH.read_text())
    creds = Credentials.from_authorized_user_info(data, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_credentials(creds)
    return creds


def has_credentials() -> bool:
    return load_credentials() is not None
