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
    subreddit       TEXT,                             -- populated for reddit sources
    category        TEXT    NOT NULL,                -- fails | animals | gaming | wins | oddly-satisfying
    title           TEXT,
    author          TEXT,
    score           REAL,                             -- raw platform engagement score (upvotes/views/plays)
    trending_score  REAL,                             -- normalized velocity score used to pick the winner
    duration_sec    REAL,
    fetched_at      TEXT    NOT NULL,                 -- ISO8601 UTC timestamp of when we pulled it
    file_path       TEXT,                             -- path to the compressed local file
    thumb_path      TEXT,                             -- path to a generated thumbnail jpg
    status          TEXT    NOT NULL DEFAULT 'new',   -- new | selected | used | rejected
    compilation_id  INTEGER,                          -- set once included in a built compilation
    UNIQUE(source, source_id),
    FOREIGN KEY (compilation_id) REFERENCES compilations(id)
);

CREATE INDEX IF NOT EXISTS idx_clips_fetched_at ON clips(fetched_at);
CREATE INDEX IF NOT EXISTS idx_clips_source_id ON clips(source, source_id);
CREATE INDEX IF NOT EXISTS idx_clips_category ON clips(category);

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
    clip_ids        TEXT    NOT NULL,                 -- JSON list of clip ids, in stitch order
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
    kind            TEXT    NOT NULL,                 -- 'build' | 'upload'
    ref_id          INTEGER,                           -- compilation id, once known
    status          TEXT    NOT NULL DEFAULT 'pending', -- pending | running | done | error
    message         TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);
