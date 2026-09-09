# meme-pipeline

Scrapes trending meme/fail/animal/gaming/wins clips from Reddit, YouTube
and Vimeo once an hour, lets you review and pick clips from a local web
dashboard, stitches the picks into a vertical YouTube Shorts compilation
with synthesized sound effects at each cut, and uploads the result to
YouTube -- all running unattended on a 2GB DigitalOcean droplet.

## How it fits together

```
scraper/run_hourly.py   systemd timer, hourly
  ├─ reddit_source.py     PRAW, hot+rising across ~28 curated subreddits
  ├─ youtube_source.py    Data API: mostPopular chart + 1 CC search/run
  ├─ vimeo_source.py      1 CC-filtered search/run
  └─ rank.py               picks the single highest "velocity" candidate
       (engagement / hours-since-posted) that hasn't been pulled in the
       last 24h (db.is_duplicate against the `clips` table)
     -> downloader.py: yt-dlp fetch, ffmpeg H.264 compress, thumbnail,
        stored at media/YYYY-MM-DD/<category>/, JSON sidecar for
        provenance, row in SQLite `clips`

app.py (Flask, gunicorn)      dashboard, bound to 127.0.0.1:8080 only
  ├─ /review                   today's (or any date's) clips by category,
  │                            checkboxes -> "Build compilation"
  ├─ /build                    background thread -> compiler/build.py
  ├─ /compilations             preview finished MP4s, upload to YouTube
  └─ /youtube/authorize        OAuth flow for youtube_upload/upload.py

compiler/
  ├─ sfx_gen.py    synthesizes a handful of whoosh/pop/ding effects with
  │                ffmpeg (no external audio assets -- avoids any
  │                licensing question entirely)
  └─ build.py      scale/pad every clip to 720x1280 (9:16), crossfade
                   video+audio at each cut (ffmpeg xfade/acrossfade),
                   layer a random synthesized sfx on every cut, hard-cap
                   at 58s, single mp4 out
```

Everything shares one advisory file lock (`lockutil.py`) so the hourly
scrape and a dashboard-triggered build never run ffmpeg at the same time
-- important headroom on a 2GB box with only a 2GB swapfile behind it.

## Curated sources

28 subreddits mapped to 5 output categories (`scraper/subreddits.py`):

| category | subreddits |
|---|---|
| fails | PublicFreakout, instant_regret, Unexpected, WhatCouldGoWrong, IdiotsInCars, ClumsyGirls, Wellthatsucks, therewasanattempt |
| animals | AnimalsBeingDerps, AnimalsBeingBros, AnimalsBeingJerks, Zoomies, aww, awwducational |
| gaming | gaming, GamePhysics, gamingmemes, outside |
| wins | nextfuckinglevel, HumansBeingBros, MadeMeSmile, ContagiousLaughter, toptalent |
| oddly-satisfying | oddlysatisfying, perfectlycutscreams, BeAmazed, Damnthatsinteresting |

Edit that dict to add/remove subreddits or recategorize -- no other code
needs to change.

YouTube and Vimeo each contribute candidates too (see module docstrings
in `scraper/youtube_source.py` / `vimeo_source.py`), rotated by hour of
day across the same five categories so quota usage stays flat and
predictable (YouTube: ~1 expensive `search.list` call/hour, well inside
the 10,000 units/day default quota; see that file's docstring for the
exact math).

## "Most trending" ranking

Reddit upvotes, YouTube views and Vimeo plays aren't comparable numbers,
so everything is ranked by **velocity** -- `score / hours_since_posted`
-- in `scraper/rank.py`. This is a simple, tunable heuristic, not a
scientifically "correct" cross-platform trending score; adjust it there
if you want something fancier (e.g. weighting sources differently).

## Local setup (dev machine)

```bash
python3 -m venv .venv
source .venv/bin/activate      # .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env           # fill in credentials, see below
python gen_password_hash.py    # prints DASHBOARD_PASSWORD_HASH for .env
python -c "import db; db.init_db()"
python -m compiler.sfx_gen     # synthesize the sfx library
python -m scraper.run_hourly   # one manual scrape, or wait for cron/timer
python app.py                  # dashboard at http://127.0.0.1:8080
```

Needs `ffmpeg`/`ffprobe` on PATH (`apt install ffmpeg` / `brew install
ffmpeg`).

## Credentials you'll need to provide

Nothing above requires secrets to run the *code skeleton*; the scraper
sources just log a warning and return no candidates if their key is
missing, so you can bring these online one at a time.

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
sfx library, initializes the DB, and installs+enables:

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

### Systemd cheatsheet

```bash
systemctl status meme-scrape.timer meme-dashboard.service
systemctl start meme-scrape.service      # trigger a scrape right now
journalctl -u meme-scrape.service -f     # tail scrape logs
journalctl -u meme-dashboard.service -f  # tail dashboard logs
```

## Tunables

Everything in the "tunables" section of `.env.example` (durations, CRF,
output resolution, transition length, dedup window, etc.) has a sane
default baked into `config.py` -- override only what you want to change.

## Known constraints / heuristics worth knowing about

- **One clip per hour, hard cap.** `run_hourly.py` downloads exactly the
  single best-ranked, non-duplicate candidate (falling through to the
  next-best up to 5 deep if the top pick's download fails) -- by design,
  not a bug, per the "~24 clips/day" goal.
- **Trim-from-start.** If your selected clips for a compilation are
  longer than the ~58s budget allows, `compiler/build.py` proportionally
  shortens each one by keeping its first N seconds (not a smart-crop of
  the "best part"). Keep selections to a handful of clips (roughly
  6-10) for compilations that don't feel rushed.
- **Uploads default to `private`.** The dashboard's upload form lets you
  pick private/unlisted/public per upload; nothing goes live on your
  channel without you choosing "public" (or flipping it later in YouTube
  Studio).
- **No disk retention by default.** `scraper/retention.py` +
  `deploy/meme-retention.timer` will delete on-disk files (not DB rows)
  for unused clips older than 14 days, but it's opt-in -- see the bottom
  of `setup_droplet.sh`'s output for how to enable it.
