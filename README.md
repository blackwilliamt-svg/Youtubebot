# meme-pipeline

Scrapes trending meme/fail/animal/gaming/wins clips (video **and**
images/GIFs) from Reddit, TikTok and Instagram once an hour, lets you triage
and pick items from a local web dashboard, stitches the picks (plus any
manually-inserted reaction clips) into a vertical YouTube Shorts
compilation with sound effects and background music, and uploads the
result to YouTube -- all running unattended on a 2GB DigitalOcean
droplet.

## How it fits together

All three scraper sources run on **Bright Data's Scraper APIs**
(`scraper/brightdata_client.py`) rather than talking to
Reddit/TikTok/Instagram directly -- one shared bearer token, one dataset id
per source, trigger→poll→fetch per collection run. See "Credentials
you'll need to provide" below.

```
scraper/run_hourly.py   systemd timer, hourly -- pulls TWO items/run
  ├─ reddit_source.py     Bright Data Reddit Scraper API, hot listing across
  │                       29 curated subreddits, classifies each post as
  │                       video / image / gif
  ├─ tiktok_source.py     Bright Data TikTok Scraper API: a trending query
  │                       per mapped category + 1 hour-rotated category
  │                       query/hashtag search (video only)
  ├─ instagram_source.py  Bright Data Instagram Scraper API, 1 reels-only
  │                       keyword search/run (video only)
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

manual_import.py        manual "paste any video URL" import -- yt-dlp
                        probes the URL first (fails fast and clearly on
                        an unsupported site, a playlist, or a bad
                        duration), then reuses scraper/downloader.py so
                        the result lands in the exact same media/
                        layout as a scraped clip. Isolated from
                        run_hourly.py/snapshot.py -- nothing in scraper/
                        knows this module exists.

app.py (Flask, gunicorn)      dashboard, bound to 127.0.0.1:8080 only
  ├─ /triage                   one untriaged item at a time, ✓ keep or
  │                            ✗ permanently delete -- first pass filter
  ├─ /review                   kept items by category, checkboxes ->
  │                            also has the "paste a URL" import form
  ├─ /build                    sequencer: add clips/images/GIFs + manual
  │                            library/reactions/ clips, reorder with
  │                            ▲▼, submit -> background compiler/build.py
  ├─ /compilations             preview finished MP4s, upload to YouTube
  ├─ /subreddits                add/remove what the hourly scraper pulls
  │                            from -- writes through to the `subreddits`
  │                            DB table, not the hardcoded seed dict
  ├─ /tiktok-hashtags           add/remove TikTok hashtag/keyword search
  │                            terms per category -- same pattern as
  │                            /subreddits, its own DB table
  ├─ /stats                    read-only /triage keep/reject approval
  │                            rates -- Reddit per subreddit, TikTok and
  │                            Instagram each as one source-wide line
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

28 subreddits, seeded into the DB on first run, mapped to 6 fixed output
categories:

| category | subreddits |
|---|---|
| fails | PublicFreakout, instant_regret, Unexpected, WhatCouldGoWrong, IdiotsInCars, ClumsyGirls, Wellthatsucks, therewasanattempt |
| animals | AnimalsBeingDerps, AnimalsBeingBros, AnimalsBeingJerks, Zoomies, aww, awwducational |
| gaming | gaming, GamePhysics, gamingmemes, outside |
| wins | nextfuckinglevel, HumansBeingBros, MadeMeSmile, ContagiousLaughter, toptalent |
| oddly-satisfying | oddlysatisfying, perfectlycutscreams, BeAmazed, Damnthatsinteresting |
| mildly-infuriating | mildlyinfuriating |

**The `/subreddits` dashboard page is the actual way to add or remove
subreddits** -- a name + category form, and a Remove button per row.
Under the hood, the `subreddits` DB table (not the hardcoded dict) is
what every scrape run and Run Test Snapshot actually reads;
`scraper/subreddits.py`'s `DEFAULT_SUBREDDIT_CATEGORY` dict above is only
the one-time seed used the first time that table is empty. Adding one
doesn't verify it exists on Reddit -- a typo just yields nothing from
that source, same as any other empty listing; removing one only stops
future pulls, past clips from it are untouched. The six categories
themselves are **not** editable this way (deliberately) -- they're wired
into the reaction-library folder structure, the TikTok/Instagram query
rotation, and the dashboard's grouping, so a new one would need matching
changes in several other places to actually work end to end.

Each subreddit's hot+rising listings are classified per-post into
**video**, **image**, or **gif** (reddit galleries are skipped -- not
handled). TikTok and Instagram only ever contribute video candidates. Video
and image/gif are ranked on **separate velocity scales** and never
compared against each other -- see `scraper/rank.py`.

TikTok and Instagram each contribute video candidates too (see module
docstrings in `scraper/tiktok_source.py` / `instagram_source.py`), rotated
by hour of day across the same six categories so each hourly run stays
one Bright Data collection call per source. TikTok's rotated leg is also
fed that category's curated hashtags/keywords (see the next section) in
the same collection call.

## TikTok hashtags

`scraper/tiktok_hashtags.py` mirrors `scraper/subreddits.py`'s pattern
for an extra, editable set of hashtag/keyword search terms per category,
mixed into `tiktok_source.py`'s hour-rotated search leg alongside its
built-in query. **The `/tiktok-hashtags` dashboard page is the actual
way to add or remove them** -- same DB-table-is-the-source-of-truth
pattern as `/subreddits`.

## Triage approval stats

`/stats` is a read-only view over every `/triage` keep/reject decision,
tracked separately per source: Reddit broken out by individual
subreddit, TikTok and Instagram each as one source-wide line, sorted
weakest-approval-rate-first within each grouping. It's purely additive
reporting -- nothing about the triage flow itself changes. Dropping a
consistently-rejected subreddit is still a manual edit on `/subreddits`
(same for a weak hashtag on `/tiktok-hashtags`); there's no automatic
pruning. Stats recorded before this feature existed were backfilled for
kept clips only -- a `/triage` rejection permanently deletes the row, so
historical rejects aren't recoverable and only count from whenever this
feature was first deployed.

## "Most trending" ranking

Reddit upvotes, TikTok views and Instagram plays aren't comparable numbers,
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

## Manual "paste any video URL" import

`/review` also has a "paste a URL" form (a plain input + a category
picker, since there's no subreddit to infer one from) for pulling in a
video from basically anywhere -- not just Reddit/TikTok/Instagram. It's a
manual, on-demand path, not a new scraper category: nothing about it runs
on a schedule, and nothing in `scraper/` (run_hourly.py, snapshot.py, the
source modules) knows it exists.

- **`manual_import.py`** probes the URL with yt-dlp first (metadata only,
  no download) so an unsupported site, a playlist link, or a video
  outside `MIN_CLIP_DURATION_SEC`/`MAX_CLIP_DURATION_SEC` fails fast with
  a clear, specific message -- never a stack trace, never a wedged
  pipeline. yt-dlp itself is what gives this "virtually any site" reach:
  it supports thousands of extractors beyond TikTok/Instagram out of the
  box.
- A successful probe reuses `scraper/downloader.py`'s existing
  `download_and_store()` for the real download/compress/thumbnail --
  the exact same `media/YYYY-MM-DD/<category>/` layout, JSON sidecar, and
  24h dedup log (keyed under `source="manual"`) as a scraped clip gets.
  `scraper/retention.py`'s optional cleanup sweep picks it up the same
  way too, with no special-casing.
- Runs as a background job (same `/jobs/<id>` polling pattern as a build
  or a YouTube upload) since a download can take a little while; a failed
  import is logged (`log.warning`, with the URL) as well as shown in the
  job's error message.
- Lands as a normal untriaged clip (`triaged=0`) -- it goes through
  `/triage` next exactly like anything scraped, then `/review` and
  `/build` from there.

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

- **Bright Data** (all three scrape sources): https://brightdata.com ->
  create/sign into an account -> account settings gives you one API
  token (`BRIGHTDATA_API_KEY`), shared across every dataset you use.
  Then, under Bright Data's Scraper APIs (Web Scraper IDE / Datasets),
  set up one collector per source and grab its dataset id:
  - Reddit Scraper API -> `BRIGHTDATA_REDDIT_DATASET_ID`
  - TikTok Scraper API -> `BRIGHTDATA_TIKTOK_DATASET_ID`
  - Instagram Scraper API -> `BRIGHTDATA_INSTAGRAM_DATASET_ID`

  Each is billed/rate-limited separately by Bright Data even though auth
  is unified -- check your Bright Data plan for per-dataset pricing and
  concurrency limits before turning the hourly job loose.
- **YouTube OAuth client** (for *uploading* finished compilations --
  unrelated to the Bright Data YouTube scraper above): a Google Cloud
  project -> Credentials -> OAuth client ID -> type "Web application" ->
  add `YT_OAUTH_REDIRECT_URI` (default
  `http://localhost:8080/youtube/oauth2callback`) as an authorized
  redirect URI. First real upload will need the OAuth consent screen
  published (or your Google account added as a test user) since this is
  almost certainly an "unverified" app for personal use.

Put them all in `.env` (never in code, never committed -- `.env` is in
`.gitignore` and `deploy/setup_droplet.sh` `chmod 600`s it), or set/update
them live from the dashboard's **Settings** page (`/settings`) once it's
running -- those overrides take effect immediately, no restart needed, and
are stored in the local SQLite DB rather than `.env`.

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
