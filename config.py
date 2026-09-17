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
# Lowered from 120 -> 15: no clip longer than 15s should ever be pulled (short-form only).
MAX_CLIP_DURATION_SEC = int(os.environ.get("MAX_CLIP_DURATION_SEC", "15"))
MIN_CLIP_DURATION_SEC = int(os.environ.get("MIN_CLIP_DURATION_SEC", "3"))
REDDIT_LISTING_LIMIT = int(os.environ.get("REDDIT_LISTING_LIMIT", "15"))
FFMPEG_CRF = os.environ.get("FFMPEG_CRF", "25")
FFMPEG_PRESET = os.environ.get("FFMPEG_PRESET", "veryfast")
# Reddit doesn't strictly need a real UA any more now that Bright Data does the
# actual crawling/rendering (scraper/reddit_source.py), but our own plain HTTP
# image/gif downloads (scraper/downloader.py's _download_raw) still send one --
# a generic browser-ish string keeps CDNs that reject blank/"python-requests"
# user agents from silently 403'ing those downloads.
REDDIT_USER_AGENT = os.environ.get(
    "REDDIT_USER_AGENT", "Mozilla/5.0 (compatible; meme-pipeline/1.0; +https://github.com/blackwilliamt-svg/Youtubebot)"
)

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

# --- analyzer: freeform vision+audio tagging (see analyzer/) ---------------
# Local/open-source model, run via Ollama on the droplet -- no per-call API
# cost, but CPU-only inference on a small droplet will be slow; if that's a
# problem in practice, point OLLAMA_HOST at a bigger box or swap in a hosted
# vision API later (analyzer/vision.py is the only file that would need to
# change).
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "llava:7b")
ANALYZER_TIMEOUT_SEC = float(os.environ.get("ANALYZER_TIMEOUT_SEC", "180"))
ANALYZER_MAX_FRAMES = int(os.environ.get("ANALYZER_MAX_FRAMES", "16"))
ANALYZER_MAX_TAGS = int(os.environ.get("ANALYZER_MAX_TAGS", "12"))

# Motion/scene-change-based frame sampling (replaces flat-rate sampling):
# denser frames while FRAME_DIFF_THRESHOLD is exceeded between the current
# candidate frame and the last *kept* frame, sparser at rest.
FRAME_SAMPLE_REST_SEC = float(os.environ.get("FRAME_SAMPLE_REST_SEC", "0.5"))
FRAME_SAMPLE_MOTION_SEC = float(os.environ.get("FRAME_SAMPLE_MOTION_SEC", "0.25"))
FRAME_DIFF_THRESHOLD = float(os.environ.get("FRAME_DIFF_THRESHOLD", "12.0"))  # mean abs diff, 0-255 grayscale

# faster-whisper model size for the audio-transcript layer fed alongside
# frames into the tagging prompt -- "small"/"medium" run fine on CPU.
WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "small")

# --- search-term rotation + per-input result cap (Bright Data credit cap) --
# Bright Data bills per RECORD DELIVERED, not per keyword or per API call --
# two keywords in one trigger call that each return 10 rows is 20 credits,
# not "2 searches' worth". Two levers control spend, and both are applied:
#
#   1. How many search terms each source sends per run (this walks the
#      unified list round-robin -- scraper/search_terms.rotating_terms --
#      so the full list still gets covered over successive runs rather
#      than always searching the same handful).
#   2. How many records Bright Data returns per keyword (BRIGHTDATA_LIMIT_PER_INPUT,
#      sent as the trigger endpoint's own `limit_per_input` query param --
#      https://docs.brightdata.com/api-reference/rest-api/scraper/trigger-collection).
#
# At the defaults (2 terms/platform, 1 record/term): 2 x 3 platforms x 1
# record x 24 runs/day x 30 days = ~4,320 credits/month -- between the
# 3,000/month target and Bright Data's 5,000/month free-tier ceiling.
SEARCH_TERMS_PER_RUN = int(os.environ.get("SEARCH_TERMS_PER_RUN", "2"))
BRIGHTDATA_LIMIT_PER_INPUT = int(os.environ.get("BRIGHTDATA_LIMIT_PER_INPUT", "1"))

# --- adaptive search-parameter system ---------------------------------
# Flat vote count (not "every N") at which a tag/subreddit gets auto
# added (3 likes) or auto removed (3 dislikes) from the search-parameter
# list -- intentionally not 1, so a one-off viral outlier can't skew it.
# An ordinary /triage keep/reject counts as 1 vote; a weekly-review vote
# (below) counts for more, so it can cross this threshold on its own.
ADAPTIVE_VOTE_THRESHOLD = int(os.environ.get("ADAPTIVE_VOTE_THRESHOLD", "3"))

# --- weekly feedback loop (scraper/weekly_review.py, /weekly-review) -------
# Once a week, a curated batch of clips is surfaced for a deliberate
# up/down vote pass -- deliberately including the bot's own top-ranked
# ("hottest") picks for that week, not a purely random sample, so the vote
# can be compared against what the bot already believed. These votes are
# weighted more heavily than an ordinary /triage keep/reject (weight 1) in
# both the adaptive tag/subreddit system and the engagement score, because
# they're a considered, reflective sample rather than an in-the-moment
# first-pass filter -- and a downvote on one of the bot's own top picks
# (a real disagreement, not routine feedback) is weighted heavier still.
WEEKLY_REVIEW_BATCH_MIN = int(os.environ.get("WEEKLY_REVIEW_BATCH_MIN", "12"))
WEEKLY_REVIEW_BATCH_MAX = int(os.environ.get("WEEKLY_REVIEW_BATCH_MAX", "24"))
# At least this many of the batch are the bot's own top engagement-score
# picks for the week; the rest fill in with the next-best clips so the
# batch still reaches WEEKLY_REVIEW_BATCH_MIN even with few "hottest"
# candidates available.
WEEKLY_REVIEW_TOP_PICK_COUNT = int(os.environ.get("WEEKLY_REVIEW_TOP_PICK_COUNT", "12"))
WEEKLY_REVIEW_VOTE_WEIGHT = int(os.environ.get("WEEKLY_REVIEW_VOTE_WEIGHT", "3"))
WEEKLY_REVIEW_DIVERGENCE_WEIGHT = int(os.environ.get("WEEKLY_REVIEW_DIVERGENCE_WEIGHT", "6"))
# How much a net feedback_score point (see the clips table) shifts a
# clip's composite engagement_score, which is on a roughly [-2, +2]
# z-score scale -- kept small by default so a handful of weekly votes
# nudge ranking rather than swamp it outright.
FEEDBACK_SCORE_WEIGHT = float(os.environ.get("FEEDBACK_SCORE_WEIGHT", "0.5"))

# --- composite engagement scoring (compilation clip ranking) ---------------
# Per-platform z-score normalized weighted sum -- see scraper/engagement.py.
ENGAGEMENT_WEIGHT_LIKES = float(os.environ.get("ENGAGEMENT_WEIGHT_LIKES", "0.5"))
ENGAGEMENT_WEIGHT_COMMENTS = float(os.environ.get("ENGAGEMENT_WEIGHT_COMMENTS", "0.3"))
ENGAGEMENT_WEIGHT_SHARES = float(os.environ.get("ENGAGEMENT_WEIGHT_SHARES", "0.2"))

# --- automated (trust-dial) compilation builder ----------------------------
# Separate from MAX_COMPILATION_SEC above, which caps the *manual* /build
# screen's output -- the automated builder (compiler/auto_build.py) targets
# its own 60-90s window per the spec, picking clips by composite engagement
# score until it lands in range.
AUTO_COMPILATION_MIN_SEC = float(os.environ.get("AUTO_COMPILATION_MIN_SEC", "60"))
AUTO_COMPILATION_MAX_SEC = float(os.environ.get("AUTO_COMPILATION_MAX_SEC", "90"))

# Clip-selection "trust dial" -- see autonomy.py. manual = you build every
# compilation by hand from /build; assisted = the pipeline tells you when
# enough top-ranked material exists but still waits for you; autonomous =
# it builds (but does not upload) automatically. Thumbnail selection will
# reuse this same manual/assisted/autonomous pattern later (not wired up
# yet -- see README).
AUTONOMY_LEVELS = ("manual", "assisted", "autonomous")
DEFAULT_AUTONOMY_LEVEL = os.environ.get("DEFAULT_AUTONOMY_LEVEL", "manual")
