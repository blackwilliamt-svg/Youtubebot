-- meme-pipeline SQLite schema
-- Applied automatically on first run by db.py:init_db()

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Every clip we ever pull (successfully downloaded) is recorded here.
-- This table doubles as the rolling 24h dedup log: before downloading a
-- candidate, we check for a row with the same (source, source_id) whose
-- fetched_at is within the last 24 hours.
CREATE TABLE IF NOT EXISTS clips (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT    NOT NULL,               -- 'reddit' | 'youtube' | 'vimeo'
    source_id       TEXT    NOT NULL,                -- platform-native id (submission id / video id)
    source_url      TEXT    NOT NULL,                -- permalink to the original post/video
    media_url       TEXT,                            -- direct/resolved media url used for download
    media_type      TEXT    NOT NULL DEFAULT 'video', -- 'video' | 'image' | 'gif'
    subreddit       TEXT,                             -- populated for reddit sources
    category        TEXT    NOT NULL,                -- fails | animals | gaming | wins | oddly-satisfying | mildly-infuriating
    title           TEXT,
    author          TEXT,
    score           REAL,                             -- raw platform engagement score (upvotes/views/plays)
    trending_score  REAL,                             -- normalized velocity score used to pick the winner
    duration_sec    REAL,
    fetched_at      TEXT    NOT NULL,                 -- ISO8601 UTC timestamp of when we pulled it
    file_path       TEXT,                             -- path to the compressed local file
    thumb_path      TEXT,                             -- path to a generated thumbnail jpg
    status          TEXT    NOT NULL DEFAULT 'new',   -- new | selected | used | rejected
    triaged         INTEGER NOT NULL DEFAULT 0,       -- 0 = awaiting /triage accept-or-delete pass, 1 = kept
    compilation_id  INTEGER,                          -- set once included in a built compilation
    likes           REAL,                              -- raw likes/upvotes, kept separate from `score` for engagement scoring
    comments        REAL,                              -- raw comment count, when the source dataset exposes one
    shares          REAL,                              -- raw share count, when the source dataset exposes one
    engagement_score REAL,                             -- composite, per-platform z-score weighted (scraper/engagement.py)
    tags            TEXT,                               -- JSON list of freeform tags from the analyzer (analyzer/)
    transcript      TEXT,                               -- Whisper speech transcript, when the clip has audio
    feedback_score  REAL    NOT NULL DEFAULT 0,          -- weighted human feedback (weekly review votes -- see
                                                          -- scraper/weekly_review.py), folded into engagement_score
    UNIQUE(source, source_id),
    FOREIGN KEY (compilation_id) REFERENCES compilations(id)
);

CREATE INDEX IF NOT EXISTS idx_clips_fetched_at ON clips(fetched_at);
CREATE INDEX IF NOT EXISTS idx_clips_source_id ON clips(source, source_id);
CREATE INDEX IF NOT EXISTS idx_clips_category ON clips(category);
CREATE INDEX IF NOT EXISTS idx_clips_triaged ON clips(triaged);

-- Every hourly run is logged here too, even hours where nothing qualified,
-- so you can see gaps/failures at a glance from the dashboard or sqlite3.
CREATE TABLE IF NOT EXISTS scrape_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at          TEXT    NOT NULL,
    candidates_seen INTEGER NOT NULL DEFAULT 0,
    clip_id         INTEGER,                          -- the clip pulled this run, if any
    note            TEXT,                              -- e.g. 'no qualifying candidate', or an error message
    FOREIGN KEY (clip_id) REFERENCES clips(id)
);

CREATE TABLE IF NOT EXISTS compilations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT    NOT NULL,
    clip_ids        TEXT    NOT NULL,                 -- JSON list of sequence refs, in stitch order
                                                        -- (e.g. "clip:15", "reaction:fails/boing.mp4")
    output_path     TEXT,
    duration_sec    REAL,
    status          TEXT    NOT NULL DEFAULT 'building', -- building | ready | failed
    error           TEXT,
    youtube_video_id TEXT,
    youtube_url     TEXT,
    uploaded_at     TEXT
);

-- Simple background-job tracker so the dashboard can poll build/upload
-- progress without holding an HTTP request open.
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT    NOT NULL,                 -- 'build' | 'upload' | 'snapshot' | 'import'
    ref_id          INTEGER,                           -- compilation id, once known
    status          TEXT    NOT NULL DEFAULT 'pending', -- pending | running | done | error
    message         TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

-- Dashboard-editable API credentials/config (the /settings page). Overrides
-- the matching config.py attribute (which otherwise comes from .env) at
-- runtime -- see api_settings.py. Absent key = fall back to the .env value.
CREATE TABLE IF NOT EXISTS settings (
    key             TEXT PRIMARY KEY,
    value           TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

-- Preferred/origin subreddits -- no longer what drives Reddit scraping
-- (that's search_terms below, since Reddit sourcing switched to
-- keyword search), but kept as its own adaptive dimension: liked/disliked
-- clips bump subreddit_votes, and crossing the vote threshold adds/removes
-- a row here (scraper/adaptive.py). Still editable by hand from the
-- dashboard's Search Parameters page. Seeded once from scraper/subreddits.py's
-- DEFAULT_SUBREDDIT_CATEGORY the first time this table is empty.
CREATE TABLE IF NOT EXISTS subreddits (
    name            TEXT PRIMARY KEY,                 -- as typed, e.g. "PublicFreakout" (no "r/" prefix)
    category        TEXT    NOT NULL,                 -- one of scraper.subreddits.CATEGORIES
    added_at        TEXT    NOT NULL
);

-- The live, editable TikTok hashtag/keyword source list (dashboard's
-- /tiktok-hashtags page) -- mirrors the `subreddits` table's pattern.
-- Seeded once from scraper/tiktok_hashtags.py's DEFAULT_HASHTAG_CATEGORY
-- the first time this table is empty; after that this table is the source
-- of truth scraper/tiktok_source.py actually reads.
CREATE TABLE IF NOT EXISTS tiktok_hashtags (
    hashtag         TEXT PRIMARY KEY,                 -- as typed, e.g. "#satisfying" or "satisfying"
    category        TEXT    NOT NULL,                 -- one of scraper.subreddits.CATEGORIES
    added_at        TEXT    NOT NULL
);

-- The unified "Search Parameters" list (dashboard's /search-parameters page,
-- formerly the separate Subreddits + TikTok Hashtags tabs/tables above,
-- which are now migrated in here and kept only as one-time-migration
-- sources -- see db.py's _migrate()). One list of search terms shared by
-- Reddit (keyword search, sorted hot), TikTok, and Instagram, rather than
-- separate per-platform lists. Grown/pruned automatically by the adaptive
-- system (scraper/adaptive.py) as well as by hand.
CREATE TABLE IF NOT EXISTS search_terms (
    term            TEXT PRIMARY KEY,                 -- as typed, e.g. "meme" or "#satisfying"
    category        TEXT    NOT NULL,                 -- one of scraper.subreddits.CATEGORIES
    added_at        TEXT    NOT NULL
);

-- Running like/dislike counts per freeform analyzer tag, used by the
-- adaptive system to auto add/remove a `search_terms` entry once a tag
-- crosses config.ADAPTIVE_VOTE_THRESHOLD on either side.
CREATE TABLE IF NOT EXISTS tag_votes (
    tag             TEXT PRIMARY KEY,
    category        TEXT,                              -- most recent clip category this tag was seen tagging
    likes           INTEGER NOT NULL DEFAULT 0,
    dislikes        INTEGER NOT NULL DEFAULT 0
);

-- Running like/dislike counts per Reddit subreddit-of-origin -- its own
-- taggable dimension alongside content tags (section 3 of the spec),
-- tracked even though Reddit sourcing is keyword-based now, not
-- subreddit-list-based. Crossing the threshold adds/removes the
-- subreddit from the `subreddits` table below.
CREATE TABLE IF NOT EXISTS subreddit_votes (
    name            TEXT PRIMARY KEY,
    likes           INTEGER NOT NULL DEFAULT 0,
    dislikes        INTEGER NOT NULL DEFAULT 0
);

-- Weekly feedback loop (scraper/weekly_review.py, /weekly-review): once a
-- week, a curated batch of 12-24 clips is surfaced for the user to vote
-- on, deliberately including the bot's own top-ranked ("hottest") picks
-- for that week rather than a purely random sample -- so the vote can be
-- compared against what the bot already believed about each clip. Votes
-- here count for more than an ordinary /triage keep/reject (see
-- config.WEEKLY_REVIEW_VOTE_WEIGHT / WEEKLY_REVIEW_DIVERGENCE_WEIGHT and
-- scraper/adaptive.py), because they're a deliberate, reflective sample
-- rather than an in-the-moment first-pass filter.
CREATE TABLE IF NOT EXISTS weekly_review_batches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT    NOT NULL,
    week_label      TEXT    NOT NULL                   -- e.g. '2026-W38', for display/dedup
);

CREATE TABLE IF NOT EXISTS weekly_review_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        INTEGER NOT NULL,
    clip_id         INTEGER NOT NULL,
    is_top_pick     INTEGER NOT NULL DEFAULT 0,         -- 1 = one of the bot's self-identified top engagement picks
                                                          -- that week; a vote disagreeing with this is the strongest
                                                          -- signal (config.WEEKLY_REVIEW_DIVERGENCE_WEIGHT)
    vote            TEXT,                                -- NULL (not yet voted) | 'up' | 'down'
    voted_at        TEXT,
    UNIQUE(batch_id, clip_id),
    FOREIGN KEY (batch_id) REFERENCES weekly_review_batches(id),
    FOREIGN KEY (clip_id) REFERENCES clips(id)
);

-- Per-source triage approval stats, updated on every /triage keep or
-- reject. Kept separate by source, and for reddit further broken out by
-- subreddit -- tiktok/instagram are each a single row ("all") since they
-- aren't list-based sources the same way. See db.py's record_triage_*
-- and get_triage_stats().
CREATE TABLE IF NOT EXISTS triage_stats (
    source          TEXT    NOT NULL,                 -- 'reddit' | 'tiktok' | 'instagram'
    subgroup        TEXT    NOT NULL,                 -- subreddit name for reddit, 'all' for tiktok/instagram
    keeps           INTEGER NOT NULL DEFAULT 0,
    rejects         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source, subgroup)
);
