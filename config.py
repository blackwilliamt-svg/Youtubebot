"""
Central configuration. Everything secret/host-specific comes from the
environment (.env in dev, real env vars or an EnvironmentFile= in prod).
Never hardcode credentials here -- see .env.example for the full list of
variables this project reads.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# --- filesystem layout -------------------------------------------------
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media")).resolve()
DB_PATH = Path(os.environ.get("DB_PATH", BASE_DIR / "pipeline.db")).resolve()
SFX_DIR = Path(os.environ.get("SFX_DIR", BASE_DIR / "sfx")).resolve()
YT_TOKEN_PATH = Path(
    os.environ.get("YT_TOKEN_PATH", BASE_DIR / "secrets" / "youtube_token.json")
).resolve()
LOCK_DIR = Path(os.environ.get("LOCK_DIR", BASE_DIR / ".locks")).resolve()

for d in (MEDIA_ROOT, SFX_DIR, YT_TOKEN_PATH.parent, LOCK_DIR):
    d.mkdir(parents=True, exist_ok=True)

# --- reddit --------------------------------------------------------------
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.environ.get(
    "REDDIT_USER_AGENT", "meme-pipeline/1.0 (by u/change_me)"
)

# --- youtube data api (search/trending) -----------------------------------
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")

# --- youtube oauth (upload) ------------------------------------------------
YT_OAUTH_CLIENT_ID = os.environ.get("YT_OAUTH_CLIENT_ID", "")
YT_OAUTH_CLIENT_SECRET = os.environ.get("YT_OAUTH_CLIENT_SECRET", "")
YT_OAUTH_REDIRECT_URI = os.environ.get(
    "YT_OAUTH_REDIRECT_URI", "http://localhost:8080/youtube/oauth2callback"
)

# --- vimeo -----------------------------------------------------------------
VIMEO_ACCESS_TOKEN = os.environ.get("VIMEO_ACCESS_TOKEN", "")

# --- dashboard auth ----------------------------------------------------
DASHBOARD_USERNAME = os.environ.get("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASSWORD_HASH = os.environ.get("DASHBOARD_PASSWORD_HASH", "")
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "")

# --- scraping tunables -----------------------------------------------------
DEDUP_WINDOW_HOURS = int(os.environ.get("DEDUP_WINDOW_HOURS", "24"))
MAX_CLIP_DURATION_SEC = int(os.environ.get("MAX_CLIP_DURATION_SEC", "120"))
MIN_CLIP_DURATION_SEC = int(os.environ.get("MIN_CLIP_DURATION_SEC", "3"))
REDDIT_LISTING_LIMIT = int(os.environ.get("REDDIT_LISTING_LIMIT", "15"))
FFMPEG_CRF = os.environ.get("FFMPEG_CRF", "25")
FFMPEG_PRESET = os.environ.get("FFMPEG_PRESET", "veryfast")

# --- compilation output ---------------------------------------------------
OUTPUT_WIDTH = int(os.environ.get("OUTPUT_WIDTH", "720"))
OUTPUT_HEIGHT = int(os.environ.get("OUTPUT_HEIGHT", "1280"))
MAX_COMPILATION_SEC = int(os.environ.get("MAX_COMPILATION_SEC", "58"))
TRANSITION_SEC = float(os.environ.get("TRANSITION_SEC", "0.4"))

# --- misc --------------------------------------------------------------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
YTDLP_COOKIES_FILE = os.environ.get("YTDLP_COOKIES_FILE", "")  # optional
