"""
Flask review dashboard. Bound to 127.0.0.1 only (see deploy/meme-dashboard.service)
-- reach it from your laptop with:

    ssh -L 8080:localhost:8080 <user>@159.223.116.60

then open http://localhost:8080

Single-user login (DASHBOARD_USERNAME / DASHBOARD_PASSWORD_HASH in .env,
see gen_password_hash.py to create the hash). Builds and YouTube uploads
run on background threads with progress tracked in the `jobs` table so
the browser can poll instead of holding a request open.
"""
import functools
import logging
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                    request, send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import account_settings
import api_settings
import config
import db
import library
from compiler.build import BuildError, build_compilation
from manual_import import ManualImportError, import_url
from manual_upload import ManualUploadError, import_file
from scraper.snapshot import run_test_snapshot
from scraper import subreddits as subreddits_module
from scraper import tiktok_hashtags as hashtags_module
from scraper.subreddits import CATEGORIES
from youtube_upload import oauth as yt_oauth
from youtube_upload.upload import UploadError, upload_video

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("meme_pipeline.app")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_BYTES
if not config.FLASK_SECRET_KEY:
    log.warning("FLASK_SECRET_KEY not set in .env -- using an insecure dev fallback; sessions won't survive a restart.")
app.secret_key = config.FLASK_SECRET_KEY or "dev-only-insecure-key-set-FLASK_SECRET_KEY"
if not config.DASHBOARD_PASSWORD_HASH:
    log.warning("DASHBOARD_PASSWORD_HASH not set in .env -- no password will ever match; run gen_password_hash.py.")

db.init_db()
api_settings.apply_overrides()
account_settings.apply_overrides()
library.ensure_dirs()


@app.context_processor
def _inject_nav_counts():
    if not session.get("logged_in"):
        return {}
    try:
        return {"untriaged_count": db.count_untriaged()}
    except Exception:
        return {"untriaged_count": 0}


# --- auth ------------------------------------------------------------------

def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        valid_user = username == config.DASHBOARD_USERNAME
        valid_pass = bool(config.DASHBOARD_PASSWORD_HASH) and check_password_hash(
            config.DASHBOARD_PASSWORD_HASH, password
        )
        if valid_user and valid_pass:
            session["logged_in"] = True
            return redirect(request.args.get("next") or url_for("review"))
        flash("Invalid credentials", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# --- review / selection --------------------------------------------------

@app.route("/")
@login_required
def index():
    return redirect(url_for("review"))


def _media_rel(path_str):
    """Path (as stored in the DB) -> URL path relative to MEDIA_ROOT, for the /media/<path> route."""
    if not path_str:
        return None
    try:
        return Path(path_str).resolve().relative_to(config.MEDIA_ROOT).as_posix()
    except ValueError:
        return None


@app.route("/review")
@login_required
def review():
    date_str = request.args.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    clips = db.get_clips_for_date(date_str)  # triaged_only=True by default -- untriaged items live in /triage
    for c in clips:
        c["video_rel"] = _media_rel(c.get("file_path"))
        c["thumb_rel"] = _media_rel(c.get("thumb_path"))
    by_category = {cat: [] for cat in CATEGORIES}
    for c in clips:
        by_category.setdefault(c["category"], []).append(c)
    return render_template(
        "review.html",
        date_str=date_str,
        by_category=by_category,
        available_dates=db.get_available_dates(),
    )


@app.route("/review/<int:clip_id>/delete", methods=["POST"])
@login_required
def review_delete(clip_id):
    """Permanently deletes an already-kept clip -- for when the wrong thing
    got approved in /triage. No undo, same as /triage's reject."""
    clip = _delete_clip_and_files(clip_id, "Review")
    if clip:
        flash(f"Deleted \"{clip.get('title') or '(untitled)'}\".", "success")
    else:
        flash("That clip was already gone.", "error")
    return redirect(url_for("review", date=request.form.get("date") or None))


# --- triage (first-pass accept/reject, one item at a time) -----------------

@app.route("/triage")
@login_required
def triage():
    date_str = request.args.get("date") or None  # None = oldest untriaged across all dates
    item = db.get_next_untriaged_clip(date_str)
    if item:
        item["video_rel"] = _media_rel(item.get("file_path"))
    remaining = db.count_untriaged(date_str)
    return render_template("triage.html", item=item, remaining=remaining, date_str=date_str)


@app.route("/triage/<int:clip_id>/keep", methods=["POST"])
@login_required
def triage_keep(clip_id):
    clip = db.get_clip(clip_id)
    db.set_triaged(clip_id, True)
    if clip:
        db.record_triage_keep(clip["source"], clip.get("subreddit"))
    return redirect(url_for("triage", date=request.form.get("date") or None))


def _delete_clip_and_files(clip_id: int, log_label: str) -> dict | None:
    """Deletes a clip's DB row and its on-disk file/thumbnail/JSON sidecar
    (permanently -- no undo). Returns the deleted clip dict, or None if it
    didn't exist. Shared by /triage's reject and /review's delete."""
    clip = db.delete_clip(clip_id)
    if clip:
        for key in ("file_path", "thumb_path"):
            p = clip.get(key)
            if p:
                Path(p).unlink(missing_ok=True)
        if clip.get("file_path"):
            Path(clip["file_path"]).with_suffix(".json").unlink(missing_ok=True)
        log.info("%s: deleted clip id=%d (%s)", log_label, clip_id, clip.get("title", ""))
    return clip


@app.route("/triage/<int:clip_id>/reject", methods=["POST"])
@login_required
def triage_reject(clip_id):
    clip = _delete_clip_and_files(clip_id, "Triage")
    if clip:
        db.record_triage_reject(clip["source"], clip.get("subreddit"))
    return redirect(url_for("triage", date=request.form.get("date") or None))


# --- build sequencer (pick clips/images/gifs + manually-inserted reaction
# clips, arrange the order, then hand the ordered ref list to /build/run) ---

def _resolve_sequence(refs: list[str]) -> list[dict]:
    """
    ref -> resolved item dict with the fields compiler.build.build_compilation
    needs. "clip:<id>" resolves against the DB; "reaction:<category>/<file>"
    resolves against library/reactions/ (path-traversal guarded). Unknown
    or missing refs are silently dropped.
    """
    items = []
    for ref in refs:
        kind, _, rest = ref.partition(":")
        if kind == "clip":
            if not rest.isdigit():
                continue
            clip = db.get_clip(int(rest))
            if not clip or not clip.get("file_path"):
                continue
            items.append({
                "ref": ref, "id": clip["id"], "media_type": clip["media_type"],
                "category": clip["category"], "file_path": clip["file_path"],
                "title": clip.get("title") or "",
            })
        elif kind == "reaction":
            try:
                path = (config.REACTIONS_LIBRARY_DIR / rest).resolve()
                path.relative_to(config.REACTIONS_LIBRARY_DIR.resolve())
            except (ValueError, OSError):
                continue
            if not path.exists():
                continue
            category = rest.split("/", 1)[0]
            media_type = "gif" if path.suffix.lower() == ".gif" else "video"
            items.append({
                "ref": ref, "id": None, "media_type": media_type,
                "category": category, "file_path": str(path),
                "title": path.stem,
            })
    return items


@app.route("/build")
@login_required
def build_screen():
    date_str = request.args.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    requested_ids = [v for v in request.args.getlist("clip_id") if v.isdigit()]
    initial_items = _resolve_sequence([f"clip:{i}" for i in requested_ids])
    for it in initial_items:
        rel = _media_rel(it["file_path"])
        it["url"] = url_for("media", subpath=rel) if rel else ""

    clips = db.get_clips_for_date(date_str)
    for c in clips:
        rel = _media_rel(c.get("file_path"))
        c["url"] = url_for("media", subpath=rel) if rel else ""
    by_category = {cat: [] for cat in CATEGORIES}
    for c in clips:
        by_category.setdefault(c["category"], []).append(c)

    reactions_by_category = {
        cat: [p.name for p in paths] for cat, paths in library.list_reaction_clips().items()
    }

    return render_template(
        "build.html",
        date_str=date_str,
        by_category=by_category,
        reactions_by_category=reactions_by_category,
        initial_items=initial_items,
        available_dates=db.get_available_dates(),
        soft_warning_count=10,
    )


@app.route("/build/run", methods=["POST"])
@login_required
def build_run():
    refs = request.form.getlist("ref")
    items = _resolve_sequence(refs)
    if not items:
        flash("Add at least one item to the sequence first.", "error")
        return redirect(url_for("build_screen"))

    job_id = db.create_job("build")

    def _worker():
        db.update_job(job_id, status="running", message="Building compilation...")
        try:
            result = build_compilation(items)
            db.update_job(job_id, status="done", ref_id=result["id"], message="Ready")
        except BuildError as exc:
            db.update_job(job_id, status="error", message=str(exc))
        except Exception:
            log.exception("Unexpected error in build job %d", job_id)
            db.update_job(job_id, status="error", message="Unexpected error, check server logs")

    threading.Thread(target=_worker, daemon=True).start()
    return redirect(url_for("job_status_page", job_id=job_id))


# --- manual test snapshot ---------------------------------------------

@app.route("/snapshot/run", methods=["POST"])
@login_required
def snapshot_run():
    job_id = db.create_job("snapshot")

    def _progress(category, total_so_far):
        db.update_job(job_id, message=f"Running... ({category} done, {total_so_far} item(s) so far)")

    def _worker():
        db.update_job(job_id, status="running", message="Running...")
        try:
            result = run_test_snapshot(progress_cb=_progress)
            if "error" in result:
                db.update_job(job_id, status="error", message=result["error"])
            else:
                db.update_job(job_id, status="done", message=f"Done - {result['pulled']} item(s) pulled")
        except Exception:
            log.exception("Unexpected error in snapshot job %d", job_id)
            db.update_job(job_id, status="error", message="Unexpected error, check server logs")

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({"job_id": job_id})


# --- manual "paste any video URL" import -------------------------------
# Additive and isolated from the scrape/rank/dedup pipeline -- see
# manual_import.py's module docstring. Reuses the same background-job +
# /jobs/<id> polling pattern as /build/run and the YouTube upload below,
# and lands the result in the exact same triaged=0 state a scraped clip
# gets, so it goes through /triage next like anything else.

@app.route("/import/url", methods=["POST"])
@login_required
def import_url_run():
    url = (request.form.get("url") or "").strip()
    category = (request.form.get("category") or "").strip()
    if not url:
        flash("Paste a URL first.", "error")
        return redirect(url_for("review"))
    if category not in CATEGORIES:
        flash("Pick a category for the import.", "error")
        return redirect(url_for("review"))

    job_id = db.create_job("import")

    def _worker():
        db.update_job(job_id, status="running", message=f"Downloading {url} ...")
        try:
            clip_id = import_url(url, category)
            db.update_job(job_id, status="done", ref_id=clip_id,
                           message="Imported -- check Triage to keep or delete it.")
        except ManualImportError as exc:
            db.update_job(job_id, status="error", message=str(exc))
        except Exception:
            log.exception("Unexpected error importing %s", url)
            db.update_job(job_id, status="error", message="Unexpected error, check server logs")

    threading.Thread(target=_worker, daemon=True).start()
    return redirect(url_for("job_status_page", job_id=job_id))


# --- manual "upload your own footage" import ----------------------------
# Additive, like /import/url above, but starts from a file the browser
# uploads instead of a URL yt-dlp fetches -- see manual_upload.py's module
# docstring. Lands triaged=1 (straight to /review, no /triage pass) since
# it's footage you picked and uploaded on purpose.

@app.route("/import/file", methods=["POST"])
@login_required
def import_file_run():
    upload = request.files.get("video")
    category = (request.form.get("category") or "").strip()
    title = (request.form.get("title") or "").strip()

    if not upload or not upload.filename:
        flash("Choose a video file first.", "error")
        return redirect(url_for("review"))
    if category not in CATEGORIES:
        flash("Pick a category for the upload.", "error")
        return redirect(url_for("review"))

    original_filename = secure_filename(upload.filename) or "upload"
    suffix = Path(original_filename).suffix or ".mp4"
    tmp_dir = Path(tempfile.mkdtemp(prefix="meme_upload_"))
    tmp_path = tmp_dir / f"src{suffix}"
    upload.save(tmp_path)

    job_id = db.create_job("import")

    def _worker():
        db.update_job(job_id, status="running", message=f"Processing {original_filename} ...")
        try:
            clip_id = import_file(tmp_path, original_filename, category, title=title)
            db.update_job(job_id, status="done", ref_id=clip_id,
                           message="Uploaded -- check Review to add it to a build.")
        except ManualUploadError as exc:
            db.update_job(job_id, status="error", message=str(exc))
        except Exception:
            log.exception("Unexpected error importing uploaded file %s", original_filename)
            db.update_job(job_id, status="error", message="Unexpected error, check server logs")
        finally:
            tmp_path.unlink(missing_ok=True)
            try:
                tmp_dir.rmdir()
            except OSError:
                pass  # not empty / already gone -- fine, it's a scratch dir

    threading.Thread(target=_worker, daemon=True).start()
    return redirect(url_for("job_status_page", job_id=job_id))


# --- subreddit source list (add/remove what the hourly scraper pulls) ------

@app.route("/subreddits")
@login_required
def subreddits():
    by_category = {cat: [] for cat in CATEGORIES}
    for s in subreddits_module.all_subreddits_with_categories():
        by_category.setdefault(s["category"], []).append(s)
    return render_template("subreddits.html", by_category=by_category)


@app.route("/subreddits/add", methods=["POST"])
@login_required
def subreddits_add():
    name = request.form.get("name", "")
    category = request.form.get("category", "")
    try:
        stored_name = subreddits_module.add_subreddit(name, category)
        flash(f"Added r/{stored_name} to {category}.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("subreddits"))


@app.route("/subreddits/<path:name>/remove", methods=["POST"])
@login_required
def subreddits_remove(name):
    subreddits_module.remove_subreddit(name)
    flash(f"Removed r/{name}. Already-pulled clips from it are untouched.", "success")
    return redirect(url_for("subreddits"))


@app.route("/tiktok-hashtags")
@login_required
def tiktok_hashtags():
    by_category = {cat: [] for cat in CATEGORIES}
    for h in hashtags_module.all_hashtags_with_categories():
        by_category.setdefault(h["category"], []).append(h)
    return render_template("tiktok_hashtags.html", by_category=by_category)


@app.route("/tiktok-hashtags/add", methods=["POST"])
@login_required
def tiktok_hashtags_add():
    tag = request.form.get("hashtag", "")
    category = request.form.get("category", "")
    try:
        stored_tag = hashtags_module.add_hashtag(tag, category)
        flash(f"Added {stored_tag} to {category}.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("tiktok_hashtags"))


@app.route("/tiktok-hashtags/<path:tag>/remove", methods=["POST"])
@login_required
def tiktok_hashtags_remove(tag):
    hashtags_module.remove_hashtag(tag)
    flash(f"Removed {tag}. Already-pulled clips from it are untouched.", "success")
    return redirect(url_for("tiktok_hashtags"))


# --- triage approval stats (read-only, per source) -------------------------

@app.route("/stats")
@login_required
def stats_page():
    all_stats = db.get_triage_stats()
    reddit_stats = [s for s in all_stats if s["source"] == "reddit"]
    tiktok_stats = next((s for s in all_stats if s["source"] == "tiktok"), None)
    instagram_stats = next((s for s in all_stats if s["source"] == "instagram"), None)
    return render_template(
        "stats.html",
        reddit_stats=reddit_stats,
        tiktok_stats=tiktok_stats,
        instagram_stats=instagram_stats,
    )


# --- account (dashboard login credentials -- separate from /settings'
# third-party API creds; see account_settings.py) --------------------------

_MIN_PASSWORD_LENGTH = 8


@app.route("/account")
@login_required
def account_page():
    return render_template("account.html", current_username=config.DASHBOARD_USERNAME)


@app.route("/account/update", methods=["POST"])
@login_required
def account_update():
    current_password = request.form.get("current_password", "")
    new_username_raw = request.form.get("new_username", "")
    new_username = new_username_raw.strip()
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    try:
        current_ok = bool(config.DASHBOARD_PASSWORD_HASH) and check_password_hash(
            config.DASHBOARD_PASSWORD_HASH, current_password
        )
    except ValueError:
        current_ok = False  # malformed/empty stored hash -- never treat as a match
    if not current_ok:
        flash("Current password is incorrect.", "error")
        return redirect(url_for("account_page"))

    if new_username_raw and not new_username:
        flash("Username can't be blank.", "error")
        return redirect(url_for("account_page"))

    if new_password or confirm_password:
        if new_password != confirm_password:
            flash("New password and confirmation don't match.", "error")
            return redirect(url_for("account_page"))
        if len(new_password) < _MIN_PASSWORD_LENGTH:
            flash(f"New password must be at least {_MIN_PASSWORD_LENGTH} characters.", "error")
            return redirect(url_for("account_page"))

    if not new_username and not new_password:
        flash("Nothing to change -- set a new username and/or a new password first.", "error")
        return redirect(url_for("account_page"))

    if new_username:
        account_settings.save("DASHBOARD_USERNAME", new_username)
    if new_password:
        account_settings.save("DASHBOARD_PASSWORD_HASH", generate_password_hash(new_password))

    session.clear()
    flash("Account updated -- log in again with your new credentials.", "success")
    return redirect(url_for("login"))


# --- settings (dashboard-editable API credentials) ------------------------

@app.route("/settings")
@login_required
def settings_page():
    return render_template("settings.html", groups=api_settings.grouped_fields())


@app.route("/settings/save", methods=["POST"])
@login_required
def settings_save():
    key = request.form.get("key", "")
    value = request.form.get("value", "").strip()
    field = next((f for f in api_settings.API_FIELDS if f["key"] == key), None)
    if not field:
        abort(404)
    if not value:
        flash("Enter a value first.", "error")
        return redirect(url_for("settings_page"))
    api_settings.save(key, value)
    flash(f"Saved {field['group']} {field['label']}.", "success")
    return redirect(url_for("settings_page"))


@app.route("/settings/<key>/reset", methods=["POST"])
@login_required
def settings_reset(key):
    field = next((f for f in api_settings.API_FIELDS if f["key"] == key), None)
    if not field:
        abort(404)
    api_settings.reset(key)
    flash(f"Reset {field['group']} {field['label']} to its .env default.", "success")
    return redirect(url_for("settings_page"))


# --- compilations / upload -------------------------------------------------

@app.route("/compilations")
@login_required
def compilations():
    comps = db.list_compilations()
    for c in comps:
        c["output_rel"] = _media_rel(c.get("output_path"))
    return render_template(
        "compilations.html",
        compilations=comps,
        youtube_connected=yt_oauth.has_credentials(),
        youtube_configured=yt_oauth.is_configured(),
    )


_THUMBNAIL_EXTENSIONS = {".jpg", ".jpeg", ".png"}


@app.route("/compilations/<int:compilation_id>/upload", methods=["POST"])
@login_required
def upload_compilation(compilation_id):
    comp = db.get_compilation(compilation_id)
    if not comp or comp["status"] != "ready":
        abort(404)

    title = request.form.get("title") or f"Meme Compilation {comp['created_at'][:10]}"
    description = request.form.get("description", "")
    privacy = request.form.get("privacy_status", "private")

    # Optional custom thumbnail -- save it to a temp file now (while the
    # request's file storage is still alive) so the background thread has
    # a plain path to hand to MediaFileUpload; cleaned up after the upload
    # job finishes either way.
    thumbnail_path = None
    thumb_upload = request.files.get("thumbnail")
    if thumb_upload and thumb_upload.filename:
        ext = Path(secure_filename(thumb_upload.filename)).suffix.lower()
        if ext not in _THUMBNAIL_EXTENSIONS:
            flash(f"Thumbnail must be a jpg or png (got {ext or 'no extension'}) -- uploading video without it.", "error")
        else:
            tmp_dir = Path(tempfile.mkdtemp(prefix="meme_thumb_"))
            thumbnail_path = tmp_dir / f"thumb{ext}"
            thumb_upload.save(thumbnail_path)
            if thumbnail_path.stat().st_size > config.MAX_THUMBNAIL_BYTES:
                flash(
                    f"Thumbnail is over YouTube's {config.MAX_THUMBNAIL_BYTES // (1024*1024)}MB limit -- "
                    "uploading video without it.", "error",
                )
                thumbnail_path.unlink(missing_ok=True)
                tmp_dir.rmdir()
                thumbnail_path = None

    job_id = db.create_job("upload", ref_id=compilation_id)

    def _worker():
        db.update_job(job_id, status="running", message="Uploading to YouTube...")
        try:
            result = upload_video(
                comp["output_path"], title, description,
                privacy_status=privacy, thumbnail_path=str(thumbnail_path) if thumbnail_path else None,
            )
            db.update_compilation(
                compilation_id,
                youtube_video_id=result["video_id"],
                youtube_url=result["url"],
                uploaded_at=db.utcnow_iso(),
            )
            message = f"Uploaded: {result['url']}"
            if result.get("thumbnail_warning"):
                message += " (uploaded, but the custom thumbnail failed to set -- see server logs)"
            db.update_job(job_id, status="done", message=message)
        except UploadError as exc:
            db.update_job(job_id, status="error", message=str(exc))
        except Exception:
            log.exception("Unexpected error in upload job %d", job_id)
            db.update_job(job_id, status="error", message="Unexpected error, check server logs")
        finally:
            if thumbnail_path:
                thumbnail_path.unlink(missing_ok=True)
                try:
                    thumbnail_path.parent.rmdir()
                except OSError:
                    pass

    threading.Thread(target=_worker, daemon=True).start()
    return redirect(url_for("job_status_page", job_id=job_id))


# --- background job polling -------------------------------------------------

@app.route("/jobs/<int:job_id>")
@login_required
def job_status_page(job_id):
    job = db.get_job(job_id)
    if not job:
        abort(404)
    return render_template("job_status.html", job=job)


@app.route("/api/jobs/<int:job_id>")
@login_required
def job_status_api(job_id):
    job = db.get_job(job_id)
    if not job:
        abort(404)
    return jsonify(job)


# --- youtube oauth -----------------------------------------------------

@app.route("/youtube/authorize")
@login_required
def youtube_authorize():
    if not yt_oauth.is_configured():
        flash("Set YT_OAUTH_CLIENT_ID / YT_OAUTH_CLIENT_SECRET in .env first.", "error")
        return redirect(url_for("compilations"))
    auth_url, state = yt_oauth.get_authorization_url()
    session["yt_oauth_state"] = state
    return redirect(auth_url)


@app.route("/youtube/oauth2callback")
@login_required
def youtube_oauth2callback():
    code = request.args.get("code")
    state = request.args.get("state")
    if not code or state != session.pop("yt_oauth_state", None):
        flash("YouTube authorization was cancelled or failed (or the link expired -- try again).", "error")
        return redirect(url_for("compilations"))
    yt_oauth.exchange_code(code)
    flash("YouTube account connected.", "success")
    return redirect(url_for("compilations"))


# --- media serving (auth-gated) ---------------------------------------

@app.route("/media/<path:subpath>")
@login_required
def media(subpath):
    # send_from_directory rejects path traversal (".."), so this is safe
    # to expose the whole media root read-only to logged-in users.
    return send_from_directory(config.MEDIA_ROOT, subpath)


@app.route("/library/<path:subpath>")
@login_required
def library_file(subpath):
    """Serves library/{sfx,reactions,music}/... for previewing reaction clips on the build screen."""
    return send_from_directory(config.LIBRARY_DIR, subpath)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=False)
