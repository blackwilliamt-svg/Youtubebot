# meme-pipeline

Scrapes trending meme/fail/animal/gaming/wins clips (video **and**
images/GIFs) from Reddit, YouTube and Vimeo once an hour, lets you triage
and pick items from a local web dashboard, stitches the picks (plus any
manually-inserted reaction clips) into a vertical YouTube Shorts
compilation with sound effects and background music, and uploads the
result to YouTube -- all running unattended on a 2GB DigitalOcean
droplet.

## How it fits together

```
scraper/run_hourly.py   systemd timer, hourly -- pulls TWO items/run
  ├─ reddit_source.py     PRAW, hot+rising across 29 curated subreddits,
  │                       classifies each post as video / image / gif
  ├─ youtube_source.py    Data API: mostPopular chart + 1 CC search/run (video only)
  ├─ vimeo_source.py      1 CC-filtered search/run (video only)
  └─ rank.py               picks the single highest "velocity" candidate
       (engagement / hours-since-posted) in EACH of two separate pools --
       video, and image/gif -- that hasn't been pulled in the last 24h
     -> downloader.py: yt-dlp for video, plain HTTP for image/gif,
        ffmpeg compress/normalize + thumbnail, stored at
        media/YYYY-MM-DD/<category>/, JSON sidecar for provenance,
        row in SQLite `clips` (tagged media_type, triaged=0)

scraper/snapshot.py     manual "Run Test Snapshot" button -- same ranking
                        and dedup, but scoped one category at a time
                        (with a delay between categories) instead of
                        across everything

app.py (Flask, gunicorn)      dashboard, bound to 127.0.0.1:8080 only
  ├─ /triage                   one untriaged item at a time, ✓ keep or
  │                            ✗ permanently delete -- first pass filter
  ├─ /review                   kept items by category, checkboxes ->
  ├─ /build                    sequencer: add clips/images/GIFs + manual
  │                            library/reactions/ clips, reorder with
  │                            ▲▼, submit -> background compiler/build.py
  ├─ /compilations             preview finished MP4s, upload to YouTube
  └─ /youtube/authorize        OAuth flow for youtube_upload/upload.py

library.py + library/         manually-curated assets you drop in yourself
  ├─ sfx/          any audio file -- picked per cut (category-hinted by
  │                filename if possible), synthesized fallback if empty
  ├─ reactions/<category>/    short clips, manually inserted from /build
  └─ music/        background beds, ducked under silent segments

compiler/
  ├─ sfx_gen.py    synthesizes a handful of whoosh/pop/ding effects with
  │                ffmpeg (no external audio assets -- avoids any
  │                licensing question entirely); library.py's picks take
  │                priority, this is just the always-available fallback
  └─ build.py      turns any image/GIF in the sequence into a short
                   silent video slide, scales/pads everything to
                   720x1280 (9:16), crossfades video+audio at each cut
                   (ffmpeg xfade/acrossfade), layers a sfx on every cut,
                   ducks in background music under segments with no
                   native audio, hard-caps at 58s, single mp4 out
```

Everything shares one advisory file lock (`lockutil.py`) so the hourly
scrape, a manual test snapshot, and a dashboard-triggered build never run
ffmpeg at the same time -- important headroom on a 2GB box with only a
2GB swapfile behind it.

## Curated sources

29 subreddits mapped to 6 output categories (`scraper/subreddits.py`):

| category | subreddits |
|---|---|
| fails | PublicFreakout, instant_regret, Unexpected, WhatCouldGoWrong, IdiotsInCars, ClumsyGirls, Wellthatsucks, therewasanattempt |
| animals | AnimalsBeingDerps, AnimalsBeingBros, AnimalsBeingJerks, Zoomies, aww, awwducational |
| gaming | gaming, GamePhysics, gamingmemes, outside |
| wins | nextfuckinglevel, HumansBeingBros, MadeMeSmile, ContagiousLaughter, toptalent |
| oddly-satisfying | oddlysatisfying, perfectlycutscreams, BeAmazed, Damnthatsinteresting |
| mildly-infuriating | mildlyinfuriating |

Edit that dict to add/remove subreddits or recategorize -- no other code
needs to change.

Each subreddit's hot+rising listings are classified per-post into
**video**, **image**, or **gif** (reddit galleries are skipped -- not
handled). YouTube and Vimeo only ever contribute video candidates. Video
and image/gif are ranked on **separate velocity scales** and never
compared against each other -- see `scraper/rank.py`.

YouTube and Vimeo each contribute video candidates too (see module
docstrings in `scraper/youtube_source.py` / `vimeo_source.py`), rotated
by hour of day across the same six categories so quota usage stays flat
and predictable (YouTube: ~1 expensive `search.list` call/hour, well
inside the 10,000 units/day default quota; see that file's docstring for
the exact math).

## "Most trending" ranking

Reddit upvotes, YouTube views and Vimeo plays aren't comparable numbers,
so everything is ranked by **velocity** -- `score / hours_since_posted`
-- in `scraper/rank.py`. This is a simple, tunable heuristic, not a
scientifically "correct" cross-platform trending score; adjust it there
if you want something fancier (e.g. weighting sources differently).

## Manual libraries (`library/`)

Three folders you seed yourself -- nothing is bundled, nothing is
auto-tagged beyond an optional filename hint:

- **`library/sfx/`** -- drop mp3/wav/etc; picked at random per cut,
  preferring a file whose name hints the destination clip's category
  (e.g. `fail_boing.mp3`), falling back to a generic (unhinted) file,
  falling back further to ffmpeg-synthesized effects if the library is
  empty or nothing qualifies.
- **`library/reactions/<category>/`** -- short reaction clips. These
  show up as selectable items on `/build` alongside your scraped
  clips -- **fully manual**, never auto-inserted anywhere. They count
  toward the 58s cap and the 10-item soft-warning like any other item.
- **`library/music/`** -- royalty-free/CC background tracks. When a
  segment in a compilation has no usable native audio (an image/GIF
  slide, or a genuinely silent clip), `compiler/build.py` picks one at
  random, loops it to fill the segment, and ducks it well under any sfx
  or other native audio (`MUSIC_DUCK_VOLUME` in `.env`, default 0.25).
  Skipped silently if the folder is empty.

Each has its own `README.md` in place. `.gitignore` keeps the folder
structure in the repo while excluding whatever media you actually drop
in (your reaction clips, purchased/CC music, etc. aren't automatically
publishable to GitHub).

## Triage -> Review -> Build flow

1. **`/triage`** -- one freshly-scraped item at a time (video, image, or
   GIF), full preview. ✓ **Keep** marks it reviewed (it now shows up in
   `/review`); ✗ **Delete** removes the file from disk and the DB row
   *permanently* -- not a soft "unselect". Keyboard: Y/→ to keep, N/← to
   delete. Auto-advances to the next untriaged item either way, so a
   day's haul (~48 items) goes fast.
2. **`/review`** -- kept items only, grouped by category, browse and
   check the ones you want, then "Continue to build".
3. **`/build`** -- add checked items (pre-seeded from `/review`), more
   clips, and/or reaction-library clips; use ▲▼ to arrange the exact
   order (reaction clips are only ever inserted here, manually); submit
   to kick off the ffmpeg build on a background thread.
4. **`/compilations`** -- preview the finished MP4, upload to YouTube
   (defaults to private).

## Manual test snapshot

The "Run Test Snapshot" button on `/review` does one full pass across
all 6 categories *right now* (not a simulated 24h run): for each
category, pulls the single top-ranked video **and** the single
top-ranked image/GIF currently trending in that category's sources.
Categories are processed strictly one at a time with a short delay
between them (`SNAPSHOT_CATEGORY_DELAY_SEC`, default 5s) -- it reuses
the exact same ranking, 24h dedup, and per-source rate-limit
backoff/retry as the hourly job, it just doesn't wait for the clock.
Results land in the normal `media/YYYY-MM-DD/<category>/` folder and go
through `/triage` like anything else.

## Local setup (dev machine)

```bash
python3 -m venv .venv
source .venv/bin/activate      # .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env           # fill in credentials, see below
python gen_password_hash.py    # prints DASHBOARD_PASSWORD_HASH for .env
python -c "import db; db.init_db()"
python -m compiler.sfx_gen     # synthesize the fallback sfx library
python -m scraper.run_hourly   # one manual scrape, or wait for cron/timer
python app.py                  # dashboard at http://127.0.0.1:8080
```

Needs `ffmpeg`/`ffprobe` on PATH (`apt install ffmpeg` / `brew install
ffmpeg`).

## Credentials you'll need to provide

Nothing above requires secrets to run the *code skeleton*; the scraper
sources just log a warning and return no candidates if their key is
missing, so you can bring these online one at a time. A blank `.env`
(copied from `.env.example`) is already sitting in the repo root -- fill
in whichever keys you have and restart the dashboard/timer to pick them
up.

- **Reddit**: https://www.reddit.com/prefs/apps -> "create app" -> type
  "script" -> gives you a client id + secret. Free tier, no billing.
- **YouTube Data API key**: https://console.cloud.google.com/apis/credentials
  in a Google Cloud project with "YouTube Data API v3" enabled -> API
  key (restrict it to that API).
- **YouTube OAuth client** (for uploading, separate from the API key):
  same project -> Credentials -> OAuth client ID -> type "Web
  application" -> add `YT_OAUTH_REDIRECT_URI` (default
  `http://localhost:8080/youtube/oauth2callback`) as an authorized
  redirect URI. First real upload will need the OAuth consent screen
  published (or your Google account added as a test user) since this is
  almost certainly an "unverified" app for personal use.
- **Vimeo**: https://developer.vimeo.com/apps -> create an app ->
  generate an access token with the default "public" scope.

Put them all in `.env` (never in code, never committed -- `.env` is in
`.gitignore` and `deploy/setup_droplet.sh` `chmod 600`s it).

## Droplet deployment

```bash
# on your laptop, once you can reach the droplet:
ssh root@159.223.116.60 "mkdir -p /opt/meme-pipeline"
git clone <this repo> # or scp/rsync the tree
ssh root@159.223.116.60
cd /opt/meme-pipeline
sudo bash deploy/setup_droplet.sh
```

`setup_droplet.sh` is idempotent: installs ffmpeg/python/sqlite, creates
a dedicated unprivileged `meme` system user, builds a venv, generates the
fallback sfx library, initializes the DB, and installs+enables:

- `meme-scrape.timer` -- runs `scraper/run_hourly.py` on the hour (with
  up to 2 minutes of random jitter so it isn't perfectly on-the-dot)
- `meme-dashboard.service` -- gunicorn bound to **127.0.0.1:8080 only**,
  restarted automatically on failure

Nothing is exposed to the public internet. Reach the dashboard by
tunneling:

```bash
ssh -L 8080:localhost:8080 root@159.223.116.60
# then open http://localhost:8080 in your own browser
```

That same tunnel is what makes the YouTube OAuth redirect
(`http://localhost:8080/youtube/oauth2callback`) work even though the
Flask app itself runs on the droplet: Google redirects your browser to
`localhost:8080`, which the SSH tunnel forwards straight to gunicorn.

After first setup, redeploy code changes with:

```bash
sudo bash deploy/deploy.sh
```

To drop in sfx/reactions/music: `scp` files straight into
`/opt/meme-pipeline/library/{sfx,reactions/<category>,music}/` on the
droplet -- no service restart needed, they're read fresh on every build.

### Systemd cheatsheet

```bash
systemctl status meme-scrape.timer meme-dashboard.service
systemctl start meme-scrape.service      # trigger a scrape right now
journalctl -u meme-scrape.service -f     # tail scrape logs
journalctl -u meme-dashboard.service -f  # tail dashboard logs
```

## Tunables

Everything in the "tunables" section of `.env.example` (durations, CRF,
output resolution, transition length, dedup window, slide duration,
music duck volume, image/gif size caps, snapshot pacing, etc.) has a sane
default baked into `config.py` -- override only what you want to change.

## Known constraints / heuristics worth knowing about

- **Two items per hour, hard cap.** `run_hourly.py` downloads exactly the
  single best-ranked, non-duplicate video AND the single best-ranked,
  non-duplicate image/GIF (falling through to the next-best up to 5 deep
  per bucket if the top pick's download fails) -- by design, not a bug,
  per the ~24 video + ~24 image/GIF per day goal.
- **Deleted in `/triage` means deleted.** There's no undo -- the file and
  DB row are gone immediately. If you're unsure, keep it and clean up
  from `/review` later instead (`/review` only ever removes items by
  putting them in a compilation, never by deleting them).
- **Trim-from-start.** If your assembled sequence is longer than the
  ~58s budget allows, `compiler/build.py` proportionally shortens each
  item by keeping its first N seconds (not a smart-crop of the "best
  part"). The `/build` screen warns past 10 items, but doesn't block you.
- **Uploads default to `private`.** The dashboard's upload form lets you
  pick private/unlisted/public per upload; nothing goes live on your
  channel without you choosing "public" (or flipping it later in YouTube
  Studio).
- **No disk retention by default.** `scraper/retention.py` +
  `deploy/meme-retention.timer` will delete on-disk files (not DB rows)
  for unused clips older than 14 days, but it's opt-in -- see the bottom
  of `setup_droplet.sh`'s output for how to enable it.
- **No vision/LLM tagging yet.** Category assignment is purely
  subreddit/query-based; a later phase may add Claude-based visual
  tagging, but that's explicitly out of scope for now.
