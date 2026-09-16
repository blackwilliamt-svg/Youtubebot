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
SFX_DIR = Path(os.environ.get("SFX_DIR", BASE_DIR / "sfx")).resolve()  # synthesized fallback sfx
YT_TOKEN_PATH = Path(
    os.environ.get("YT_TOKEN_PATH", BASE_DIR / "secrets" / "youtube_token.json")
).resolve()
LOCK_DIR = Path(os.environ.get("LOCK_DIR", BASE_DIR / ".locks")).resolve()

# --- manually-curated libraries (you drop files in, the app just scans them) ---
LIBRARY_DIR = Path(os.environ.get("LIBRARY_DIR", BASE_DIR / "library")).resolve()
SFX_LIBRARY_DIR = LIBRARY_DIR / "sfx"
REACTIONS_LIBRARY_DIR = LIBRARY_DIR / "reactions"
MUSIC_LIBRARY_DIR = LIBRARY_DIR / "music"

for d in (MEDIA_ROOT, SFX_DIR, YT_TOKEN_PATH.parent, LOCK_DIR,
          SFX_LIBRARY_DIR, REACTIONS_LIBRARY_DIR, MUSIC_LIBRARY_DIR):
    d.mkdir(parents=True, exist_ok=True)

# --- bright data (scraper apis for reddit / tiktok / instagram) --------------
# One account, one bearer token -- each source is just a different dataset
# id on the same token. See README.md for where to get each dataset id.
BRIGHTDATA_API_KEY = os.environ.get("BRIGHTDATA_API_KEY", "")
BRIGHTDATA_REDDIT_DATASET_ID = os.environ.get("BRIGHTDATA_REDDIT_DATASET_ID", "")
BRIGHTDATA_TIKTOK_DATASET_ID = os.environ.get("BRIGHTDATA_TIKTOK_DATASET_ID", "")
BRIGHTDATA_INSTAGRAM_DATASET_ID = os.environ.get("BRIGHTDATA_INSTAGRAM_DATASET_ID", "")
BRIGHTDATA_POLL_INTERVAL_SEC = float(os.environ.get("BRIGHTDATA_POLL_INTERVAL_SEC", "5"))
BRIGHTDATA_POLL_TIMEOUT_SEC = float(os.environ.get("BRIGHTDATA_POLL_TIMEOUT_SEC", "300"))

# --- youtube oauth (upload -- unaffected by the Bright Data migration) -----
YT_OAUTH_CLIENT_ID = os.environ.get("YT_OAUTH_CLIENT_ID", "")
YT_OAUTH_CLIENT_SECRET = os.environ.get("YT_OAUTH_CLIENT_SECRET", "")
YT_OAUTH_REDIRECT_URI = os.environ.get(
    "YT_OAUTH_REDIRECT_URI", "http://localhost:8080/youtube/oauth2callback"
)

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
SLIDE_DURATION_SEC = float(os.environ.get("SLIDE_DURATION_SEC", "2.5"))  # image/gif -> video slide length
MUSIC_DUCK_VOLUME = float(os.environ.get("MUSIC_DUCK_VOLUME", "0.25"))  # background music level under sfx/native audio

# --- image/gif scraping ---------------------------------------------------
MAX_IMAGE_DOWNLOAD_BYTES = int(os.environ.get("MAX_IMAGE_DOWNLOAD_BYTES", str(20 * 1024 * 1024)))
MAX_GIF_DOWNLOAD_BYTES = int(os.environ.get("MAX_GIF_DOWNLOAD_BYTES", str(40 * 1024 * 1024)))

# --- manual uploads (your own footage, and thumbnail images) --------------
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(500 * 1024 * 1024)))  # dashboard file-picker cap
MAX_THUMBNAIL_BYTES = int(os.environ.get("MAX_THUMBNAIL_BYTES", str(2 * 1024 * 1024)))  # YouTube's own thumbnail cap

# --- manual test snapshot --------------------------------------------------
SNAPSHOT_CATEGORY_DELAY_SEC = float(os.environ.get("SNAPSHOT_CATEGORY_DELAY_SEC", "5"))

# --- misc --------------------------------------------------------------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
YTDLP_COOKIES_FILE = os.environ.get("YTDLP_COOKIES_FILE", "")  # optional
