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
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                    request, send_from_directory, session, url_for)
from werkzeug.security import check_password_hash

import config
import db
from compiler.build import BuildError, build_compilation
from scraper.subreddits import CATEGORIES
from youtube_upload import oauth as yt_oauth
from youtube_upload.upload import UploadError, upload_video

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("meme_pipeline.app")

app = Flask(__name__)
if not config.FLASK_SECRET_KEY:
    log.warning("FLASK_SECRET_KEY not set in .env -- using an insecure dev fallback; sessions won't survive a restart.")
app.secret_key = config.FLASK_SECRET_KEY or "dev-only-insecure-key-set-FLASK_SECRET_KEY"
if not config.DASHBOARD_PASSWORD_HASH:
    log.warning("DASHBOARD_PASSWORD_HASH not set in .env -- no password will ever match; run gen_password_hash.py.")

db.init_db()


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
    clips = db.get_clips_for_date(date_str)
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


@app.route("/build", methods=["POST"])
@login_required
def build():
    clip_ids = [int(v) for v in request.form.getlist("clip_id")]
    if not clip_ids:
        flash("Select at least one clip first.", "error")
        return redirect(url_for("review"))

    job_id = db.create_job("build")

    def _worker():
        db.update_job(job_id, status="running", message="Building compilation...")
        try:
            result = build_compilation(clip_ids)
            db.update_job(job_id, status="done", ref_id=result["id"], message="Ready")
        except BuildError as exc:
            db.update_job(job_id, status="error", message=str(exc))
        except Exception:
            log.exception("Unexpected error in build job %d", job_id)
            db.update_job(job_id, status="error", message="Unexpected error, check server logs")

    threading.Thread(target=_worker, daemon=True).start()
    return redirect(url_for("job_status_page", job_id=job_id))


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


@app.route("/compilations/<int:compilation_id>/upload", methods=["POST"])
@login_required
def upload_compilation(compilation_id):
    comp = db.get_compilation(compilation_id)
    if not comp or comp["status"] != "ready":
        abort(404)

    title = request.form.get("title") or f"Meme Compilation {comp['created_at'][:10]}"
    description = request.form.get("description", "")
    privacy = request.form.get("privacy_status", "private")
    job_id = db.create_job("upload", ref_id=compilation_id)

    def _worker():
        db.update_job(job_id, status="running", message="Uploading to YouTube...")
        try:
            result = upload_video(comp["output_path"], title, description, privacy_status=privacy)
            db.update_compilation(
                compilation_id,
                youtube_video_id=result["video_id"],
                youtube_url=result["url"],
                uploaded_at=db.utcnow_iso(),
            )
            db.update_job(job_id, status="done", message=f"Uploaded: {result['url']}")
        except UploadError as exc:
            db.update_job(job_id, status="error", message=str(exc))
        except Exception:
            log.exception("Unexpected error in upload job %d", job_id)
            db.update_job(job_id, status="error", message="Unexpected error, check server logs")

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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=False)
