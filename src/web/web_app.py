#!/usr/bin/env python3
"""
Job Tracker Web UI
Flask web app to view and manage matched jobs
"""
import sys
import os
from datetime import datetime
from typing import Optional
from bson import ObjectId
import json

# Add src/ to path so package imports work when run as a script
_SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(_SRC_ROOT)
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

import threading
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.middleware.proxy_fix import ProxyFix
import bcrypt
from dotenv import load_dotenv
from core.db_mongo import (
    get_collection,
    ensure_indexes,
    get_scraper_stats,
    get_daily_activity_stats,
    get_applied_location_stats,
    count_applied_applications,
    get_unmatched_jobs,
    count_unmatched_jobs,
    get_timeout_jobs,
    count_open_timeout_jobs,
    save_job,
    lookup_known_job,
    _parse_filter_date,
    APPLIED_STATUSES,
    APPLICATION_QUERY,
    resolve_applied_at,
    reapplied_count,
)
from core.scraper_utils import indeed_job_id_variants
from core.manual_log import (
    IDENTITY_FIELDS,
    PLAYWRIGHT_UNAVAILABLE_MSG,
    RECOMMENDED_BY_AGENTS,
    can_fetch_details,
    job_missing_details,
    normalize_manual_identity,
    normalize_recommended_by,
    tracker_needs_scrape,
)

load_dotenv()

app = Flask(
    __name__,
    template_folder=os.path.join(_PROJECT_ROOT, "templates"),
    static_folder=os.path.join(_PROJECT_ROOT, "static"),
)
# Always pick up HTML/template edits without restarting the server
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

# Session config (required for login)
_LOCAL_SECRET_FILE = os.path.join(_PROJECT_ROOT, ".flask_secret_key")


def _load_secret_key() -> str:
    """
    FLASK_SECRET_KEY from env (Render), else a key persisted in a gitignored
    local file. A fresh random key per start would invalidate every session
    and remember-me cookie, forcing a new login after each restart.
    """
    key = (os.getenv("FLASK_SECRET_KEY") or "").strip()
    if key:
        return key
    try:
        with open(_LOCAL_SECRET_FILE, "r", encoding="utf-8") as fh:
            key = fh.read().strip()
        if key:
            return key
    except OSError:
        pass
    key = os.urandom(32).hex()
    try:
        fd = os.open(_LOCAL_SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(key)
    except OSError as exc:
        print(f"[web] Could not persist secret key ({exc}); logins reset on restart")
    return key


app.config["SECRET_KEY"] = _load_secret_key()

# Production proxy support (Render, Railway, etc. use X-Forwarded-* headers)
# This ensures Flask sees the correct scheme (https) and host when behind a reverse proxy
if os.getenv("RENDER") or os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("FLY_APP_NAME"):
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Secure session cookies for production (HTTPS)
if not app.debug and not os.getenv("FLASK_ENV") == "development":
    app.config["SESSION_COOKIE_SECURE"] = True  # Only send over HTTPS
    app.config["SESSION_COOKIE_HTTPONLY"] = True  # No JavaScript access
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"  # CSRF protection

# Authentication setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to access the job tracker."

# Simple User class for single-user auth
class User(UserMixin):
    def __init__(self, user_id):
        self.id = user_id

@login_manager.user_loader
def load_user(user_id):
    username = os.getenv("TRACKER_USERNAME")
    if username and user_id == username:
        return User(user_id)
    return None

def verify_password(username: str, password: str) -> bool:
    """Verify username and password against env vars."""
    expected_username = os.getenv("TRACKER_USERNAME")
    expected_password_hash = os.getenv("TRACKER_PASSWORD_HASH")
    
    if not expected_username or not expected_password_hash:
        print("[AUTH] Warning: TRACKER_USERNAME or TRACKER_PASSWORD_HASH not set!")
        return False
    
    if username != expected_username:
        return False
    
    try:
        return bcrypt.checkpw(password.encode('utf-8'), expected_password_hash.encode('utf-8'))
    except Exception as e:
        print(f"[AUTH] Password verification error: {e}")
        return False

# Steps that mean a job has interview progress (show as company track)
JOURNEY_INTERVIEW_STEPS = frozenset({
    "interview_invite",
    "interview_1",
    "interview_2",
    "interview_3",
    "interview_final",
    "offer",
})
# All steps shown on a company track (including Applied / Rejected)
JOURNEY_TRACK_STEPS = frozenset({
    "applied",
    "reapplied",
    "interview_invite",
    "interview_1",
    "interview_2",
    "interview_3",
    "interview_final",
    "rejected",
    "offer",
})

# ─── Status mapping ───────────────────────────────────────────────────────────
# DB value → English label
STATUS_MAP = {
    "pending":    "Not Applied",
    "applied":    "Applied",
    "rejected":   "Rejected",
    "interview":  "Interview",
    "offer":      "Offer",
    "unsuitable": "Unsuitable",
    "closed":     "Closed",
    "repost":     "Repost",
    "duplicate":  "Duplicate",
}

# Other-dropdown overlay statuses. They do not wipe an existing application
# record (applied_at / timeline); only Reset Not Applied (pending) does.
SIDE_STATUSES = ("unsuitable", "closed", "repost", "duplicate")

# Tracker chips for application progress (derived from timeline / applied_at).
PROGRESS_STAGES = APPLIED_STATUSES
INTERVIEW_TIMELINE_STEPS = (
    "interview_invite",
    "interview_1",
    "interview_2",
    "interview_3",
    "interview_final",
)

# Application progress timeline steps (ordered interview track)
# "reapplied": same job (e.g. a repost with the same id/JD) applied again
# later; the track restarts from it (Invite → Rounds / Rejected / Offer).
TIMELINE_STEPS = {
    "applied":         "Applied",
    "reapplied":       "Reapplied",
    "interview_invite": "Invite",
    "interview_1":     "Round 1",
    "interview_2":     "Round 2",
    "interview_3":     "Round 3",
    "interview_final": "Final",
    "rejected":        "Rejected",
    "offer":           "Offer",
}

TIMELINE_STEP_ORDER = (
    "applied",
    "interview_invite",
    "interview_1",
    "interview_2",
    "interview_3",
    "interview_final",
)

# Unmatched jobs user_status mapping (manual override of AI mismatch)
UNMATCHED_USER_STATUS_MAP = {
    "":           "Unmarked",
    "watchlist":  "Can Apply",
}

# English label → DB value (reverse mapping used nowhere yet, kept for clarity)
STATUS_REVERSE = {v: k for k, v in STATUS_MAP.items()}

MATCH_THRESHOLD = float(os.getenv("MATCH_THRESHOLD", "7.0"))
UNMATCHED_PAGE_SIZE = 20

# List payload omits full JD text (often multi-KB per job). Lazy-load via
# GET /api/jobs/<id>/description when the user expands "Original description".
JOBS_LIST_PROJECTION = {"description": 0}
FETCH_DETAILS_BATCH_MAX = 80

# Ensure indexes once at import (Flask reloader may import twice; ensure_indexes is idempotent)
try:
    ensure_indexes()
except Exception as exc:
    print(f"[web] ensure_indexes skipped: {exc}")


def _parse_timeline_at(value) -> Optional[datetime]:
    """Parse a timeline timestamp from ISO / 'YYYY-MM-DD HH:MM' / datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _format_timeline_at(value) -> str:
    dt = _parse_timeline_at(value)
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def _status_from_timeline(timeline: list, fallback: str = "pending") -> str:
    """Derive matched_jobs.status from the latest timeline step."""
    if not timeline:
        return fallback if fallback in (*SIDE_STATUSES, "pending") else "pending"
    last = timeline[-1].get("step", "")
    if last in ("applied", "reapplied"):
        return "applied"
    if last.startswith("interview_"):
        return "interview"
    if last == "rejected":
        return "rejected"
    if last == "offer":
        return "offer"
    return fallback


def application_progress(job: dict) -> Optional[str]:
    """
    Application stage for Tracker chips: applied / interview / rejected / offer.

    Uses timeline (then applied_at / applied-like status). Side overlays
    (unsuitable / closed / repost / duplicate) do not hide progress.
    """
    raw = job.get("application_timeline")
    if isinstance(raw, list) and raw:
        derived = _status_from_timeline(raw, fallback="")
        return derived if derived in PROGRESS_STAGES else None
    status = job.get("status") or "pending"
    if status == "pending":
        return None
    if status in PROGRESS_STAGES:
        return status
    if job.get("applied_at"):
        return "applied"
    return None


def job_matches_status_filter(job: dict, status_filter: Optional[str]) -> bool:
    """Same rules as GET /api/jobs?status= and the filter-chip counts."""
    if not status_filter or status_filter == "all":
        return True
    if status_filter in PROGRESS_STAGES:
        return application_progress(job) == status_filter
    return (job.get("status") or "pending") == status_filter


def jobs_status_filter_query(status_filter: Optional[str]) -> dict:
    """
    Mongo clause for a Tracker status chip.

    Progress stages match the latest timeline step (or a flat applied-like
    status / applied_at when there is no timeline). Overlay chips still
    match the status field so Unsuitable stays a real filter.
    """
    if not status_filter or status_filter == "all":
        return {}
    if status_filter not in PROGRESS_STAGES:
        return {"status": status_filter}

    last_step = {"$arrayElemAt": ["$application_timeline.step", -1]}
    if status_filter == "interview":
        last_matches = {"$expr": {"$in": [last_step, list(INTERVIEW_TIMELINE_STEPS)]}}
    elif status_filter == "applied":
        last_matches = {"$expr": {"$in": [last_step, ["applied", "reapplied"]]}}
    else:
        last_matches = {"$expr": {"$eq": [last_step, status_filter]}}

    no_timeline = {"application_timeline.0": {"$exists": False}}
    clauses = [
        last_matches,
        {**no_timeline, "status": status_filter},
    ]
    if status_filter == "applied":
        clauses.append({
            **no_timeline,
            "applied_at": {"$ne": None},
            "status": {"$in": list(SIDE_STATUSES)},
        })
    return {"$or": clauses}


def combine_job_list_query(clauses: list) -> dict:
    """AND independent list filters so a status $or does not clash with search $or."""
    parts = [c for c in clauses if c]
    if not parts:
        return {}
    if len(parts) == 1:
        return parts[0]
    return {"$and": parts}


def chip_status_counts(jobs: list) -> dict:
    """
    Filter-chip counts.

    Overlay chips (pending / side statuses) use the status field.
    Progress chips use application_progress, so a side-status interview
    increments both Unsuitable and Interview.
    """
    counts: dict = {}
    for job in jobs:
        status = job.get("status") or "pending"
        if status not in PROGRESS_STAGES:
            counts[status] = counts.get(status, 0) + 1
        progress = application_progress(job)
        if progress:
            counts[progress] = counts.get(progress, 0) + 1
    return counts


def _normalize_timeline(raw) -> list:
    """Validate & normalise a timeline payload into [{step, at}, ...]."""
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        step = item.get("step")
        if step not in TIMELINE_STEPS:
            continue
        at = _parse_timeline_at(item.get("at")) or datetime.now()
        out.append({"step": step, "at": at})
    return out


def _hydrate_timeline(job: dict) -> list:
    """Return timeline list; seed from applied_at for older records."""
    timeline = job.get("application_timeline")
    if isinstance(timeline, list) and timeline:
        return timeline
    status = job.get("status") or "pending"
    # Reset Not Applied means there is no application to show
    if status == "pending":
        return []
    # Backfill: older records only had a flat status (+ applied_at).
    # Side-status jobs that were applied still show that progress.
    if job.get("applied_at") or status in APPLIED_STATUSES:
        at = job.get("applied_at") or job.get("updated_at") or job.get("matched_at")
        seeded = [{"step": "applied", "at": at or datetime.now()}]
        if status == "rejected":
            seeded.append({"step": "rejected", "at": job.get("updated_at") or at or datetime.now()})
        elif status == "offer":
            seeded.append({"step": "offer", "at": job.get("updated_at") or at or datetime.now()})
        elif status == "interview":
            seeded.append({"step": "interview_1", "at": job.get("updated_at") or at or datetime.now()})
        return seeded
    return []


def _existing_applied_event(job: dict) -> Optional[dict]:
    """Return the stored Applied timeline event, or one synthesised from applied_at."""
    raw = job.get("application_timeline") if isinstance(job.get("application_timeline"), list) else []
    for item in raw:
        if isinstance(item, dict) and item.get("step") == "applied":
            at = item.get("at")
            if at:
                return {"step": "applied", "at": at}
    at = job.get("applied_at")
    if at:
        return {"step": "applied", "at": at}
    return None


def _ensure_applied_record(job: dict, timeline: list) -> list:
    """Keep the Applied step when a side-status job's payload omitted it."""
    if any(e.get("step") == "applied" for e in timeline):
        return timeline
    applied = _existing_applied_event(job)
    if not applied:
        return timeline
    return [applied, *timeline]


def _status_update_fields(job: dict, new_status: str, now: datetime) -> dict:
    """
    Fields for PATCH /status.

    Reset Not Applied (pending) clears the application record.
    Side statuses only change status — applied_at and timeline stay put.
    Entering an applied-like status stamps applied_at once, never restamps.
    """
    update_fields = {"status": new_status, "updated_at": now}
    if new_status == "pending":
        update_fields["application_timeline"] = []
        update_fields["applied_at"] = None
        return update_fields
    if new_status in APPLIED_STATUSES and not job.get("applied_at"):
        update_fields["applied_at"] = now
    return update_fields


def _timeline_update_fields(job: dict, timeline: list, now: datetime) -> dict:
    """
    Fields for PATCH /timeline.

    Side-status overlays stay in place (progress is independent of Other).
    applied_at is stamped from the Applied event when missing, synced from
    that event when the job is not on a side status, and never cleared except
    when the derived status is pending (undo back to Not Applied).
    """
    prev_status = job.get("status") or "pending"
    if prev_status in SIDE_STATUSES:
        timeline = _ensure_applied_record(job, timeline)
        new_status = prev_status
    else:
        new_status = _status_from_timeline(timeline, fallback="pending")

    update_fields = {
        "application_timeline": timeline,
        "status": new_status,
        "updated_at": now,
    }

    applied_event = next((e for e in timeline if e.get("step") == "applied"), None)
    existing_applied = job.get("applied_at")

    if new_status == "pending":
        update_fields["applied_at"] = None
    elif existing_applied:
        # Do not restamp a side-status job; allow an Applied-step edit otherwise
        if applied_event and prev_status not in SIDE_STATUSES:
            update_fields["applied_at"] = applied_event["at"]
    elif applied_event:
        update_fields["applied_at"] = applied_event["at"]
    elif timeline:
        update_fields["applied_at"] = timeline[0]["at"]

    return update_fields


def _normalize_link(job: dict) -> str:
    """
    Return a valid absolute URL for the job posting.

    LinkedIn cards store relative hrefs like /jobs/view/1234567890/
    or full URLs with tracking params. We normalise to the clean canonical form.
    """
    link = job.get("link", "")
    source = job.get("source", "")

    if source == "linkedin":
        job_id = job.get("job_id", "")
        if job_id:
            # Always use the canonical LinkedIn job URL
            return f"https://www.linkedin.com/jobs/view/{job_id}/"
        # Fallback: prepend domain if link is relative
        if link and not link.startswith("http"):
            return "https://www.linkedin.com" + link

    return link


def _serialize(job: dict) -> dict:
    """Convert MongoDB document to JSON-serialisable dict."""
    job["_id"] = str(job["_id"])
    for key in (
        "matched_at",
        "applied_at",
        "created_at",
        "updated_at",
        "description_cleared_at",
        "details_fetched_at",
    ):
        if key in job and isinstance(job[key], datetime):
            job[key] = job[key].strftime("%Y-%m-%d %H:%M")
    # Normalise job link (fixes relative LinkedIn URLs)
    job["link"] = _normalize_link(job)
    # Ensure notes field always exists
    job.setdefault("notes", "")
    # Link-only / timeout stubs wait for Tracker Run AI
    job.setdefault("ai_pending", False)
    # Manual UI highlights (user tags); separate from AI special_match
    raw_highlights = job.get("highlights")
    if not isinstance(raw_highlights, list):
        job["highlights"] = []
    else:
        job["highlights"] = [
            str(item).strip()
            for item in raw_highlights
            if str(item).strip()
        ]
    # Ensure application Q&A list always exists
    qa = job.get("application_qa")
    if not isinstance(qa, list):
        job["application_qa"] = []
    else:
        job["application_qa"] = [
            {
                "question": str(item.get("question", "")),
                "answer": str(item.get("answer", "")),
            }
            for item in qa
            if isinstance(item, dict)
        ]
    # Ensure status always exists
    job.setdefault("status", "pending")
    rec = normalize_recommended_by(job.get("recommended_by"))
    job["recommended_by"] = rec
    job["details_missing"] = job_missing_details(job)
    job["can_fetch_details"] = can_fetch_details(job)
    raw_manual = job.get("manual_fields")
    if isinstance(raw_manual, list):
        job["manual_fields"] = [
            f for f in raw_manual if f in IDENTITY_FIELDS
        ]
    else:
        job["manual_fields"] = []
    # Hydrate / serialise application timeline
    timeline = _hydrate_timeline(job)
    job["application_timeline"] = [
        {
            "step": item["step"],
            "label": TIMELINE_STEPS.get(item["step"], item["step"]),
            "at": _format_timeline_at(item.get("at")),
        }
        for item in timeline
        if isinstance(item, dict) and item.get("step") in TIMELINE_STEPS
    ]
    return job


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    """Health check endpoint for Render and other platforms."""
    return jsonify({"status": "ok", "service": "job-tracker"}), 200

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        
        if verify_password(username, password):
            user = User(username)
            login_user(user, remember=True)
            next_page = request.args.get("next")
            return redirect(next_page if next_page else url_for("index"))
        else:
            flash("Invalid username or password", "error")
    
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/unmatched")
@login_required
def unmatched():
    return render_template("unmatched.html")


@app.route("/timeouts")
@login_required
def timeouts():
    return render_template("timeouts.html")


def _jobs_list_query_from_args(args) -> dict:
    """Mongo query for Tracker list filters (status, source, search, dates, highlights)."""
    status_filter = args.get("status")
    source_filter = args.get("source")
    search_query = (args.get("q") or "").strip()
    date_from = (args.get("date_from") or "").strip() or None
    date_to = (args.get("date_to") or "").strip() or None
    highlights_filter = (args.get("highlights") or "").strip().lower()
    clauses = [jobs_status_filter_query(status_filter)]
    if source_filter and source_filter != "all":
        clauses.append({"source": source_filter})
    if highlights_filter in ("1", "true", "yes"):
        clauses.append({"highlights.0": {"$exists": True}})
    if search_query:
        clauses.append({
            "$or": [
                {"title": {"$regex": search_query, "$options": "i"}},
                {"company": {"$regex": search_query, "$options": "i"}},
            ]
        })
    if date_from or date_to:
        time_filter: dict = {}
        start = _parse_filter_date(date_from)
        end = _parse_filter_date(date_to, end_of_day=True)
        if start:
            time_filter["$gte"] = start
        if end:
            time_filter["$lte"] = end
        if time_filter:
            clauses.append({"matched_at": time_filter})
    return combine_job_list_query(clauses)


@app.route("/api/jobs")
@login_required
def api_jobs():
    """Return matched jobs with pagination, newest first."""
    collection = get_collection("matched_jobs")

    # Pagination parameters
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(request.args.get("page_size", 20))
    except (TypeError, ValueError):
        page_size = 20
    page_size = max(1, min(page_size, 100))

    query = _jobs_list_query_from_args(request.args)

    # Count total matching documents
    total = collection.count_documents(query)
    
    # Calculate pagination
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    page = min(max(1, page), pages)
    skip = (page - 1) * page_size

    jobs = list(
        collection.find(query, JOBS_LIST_PROJECTION)
        .sort("matched_at", -1)
        .skip(skip)
        .limit(page_size)
    )
    jobs = [_serialize(j) for j in jobs]

    return jsonify({
        "jobs": jobs,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    })


@app.route("/api/jobs/<job_id>/description")
@login_required
def api_job_description(job_id: str):
    """Return full JD text for one matched job (lazy-loaded by the Tracker UI)."""
    try:
        oid = ObjectId(job_id)
    except Exception:
        return jsonify({"error": "Invalid job ID"}), 400

    job = get_collection("matched_jobs").find_one({"_id": oid}, {"description": 1})
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({"description": job.get("description") or ""})


@app.route("/api/unmatched-jobs")
@login_required
def api_unmatched_jobs():
    """Return AI-rejected jobs from the original jobs collection, paginated."""
    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(request.args.get("page_size", UNMATCHED_PAGE_SIZE))
    except (TypeError, ValueError):
        page_size = UNMATCHED_PAGE_SIZE
    page_size = max(1, min(page_size, 50))

    source_filter = request.args.get("source")
    if source_filter == "all":
        source_filter = None
    search_query = request.args.get("q", "").strip()
    date_from = request.args.get("date_from", "").strip() or None
    date_to = request.args.get("date_to", "").strip() or None

    score_min = None
    score_max = None
    try:
        if request.args.get("score_min") not in (None, ""):
            score_min = float(request.args.get("score_min"))
    except (TypeError, ValueError):
        score_min = None
    try:
        if request.args.get("score_max") not in (None, ""):
            score_max = float(request.args.get("score_max"))
    except (TypeError, ValueError):
        score_max = None

    user_status_filter = request.args.get("user_status")  # "watchlist" or None
    match_gate_filter = (request.args.get("match_gate") or "").strip()
    if match_gate_filter in ("", "all"):
        match_gate_filter = None

    jobs, total = get_unmatched_jobs(
        page=page,
        page_size=page_size,
        source=source_filter,
        search=search_query or None,
        threshold=MATCH_THRESHOLD,
        date_from=date_from,
        date_to=date_to,
        score_min=score_min,
        score_max=score_max,
        user_status=user_status_filter,
        match_gate=match_gate_filter,
    )
    jobs = [_serialize(j) for j in jobs]
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    page = min(max(1, page), pages)

    return jsonify({
        "jobs": jobs,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "threshold": MATCH_THRESHOLD,
    })


@app.route("/api/unmatched-jobs/<job_id>", methods=["GET"])
@login_required
def api_get_unmatched_job(job_id: str):
    """Return one AI-scored below-threshold job for deep-link open on /unmatched."""
    try:
        oid = ObjectId(job_id)
    except Exception:
        return jsonify({"error": "Invalid job ID"}), 400

    job = get_collection("jobs").find_one(
        {
            "_id": oid,
            "matched_at": {"$exists": True},
            "match_score": {"$lt": float(MATCH_THRESHOLD)},
        },
        {"description": 0},
    )
    if not job:
        return jsonify({"error": "Unmatched job not found"}), 404
    return jsonify(_serialize(job))


@app.route("/api/jobs/<job_id>/status", methods=["PATCH"])
@login_required
def api_update_status(job_id: str):
    """Update job status. Stamps applied_at once; side statuses keep the application record."""
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    if new_status not in STATUS_MAP:
        return jsonify({"error": f"Invalid status. Allowed: {list(STATUS_MAP.keys())}"}), 400

    try:
        oid = ObjectId(job_id)
    except Exception:
        return jsonify({"error": "Invalid job ID"}), 400

    collection = get_collection("matched_jobs")
    job = collection.find_one({"_id": oid})
    if not job:
        return jsonify({"error": "Job not found"}), 404

    now = datetime.now()
    update_fields = _status_update_fields(job, new_status, now)

    collection.update_one({"_id": oid}, {"$set": update_fields})
    return jsonify({"ok": True, "status": new_status})


@app.route("/api/jobs/<job_id>/timeline", methods=["PATCH"])
@login_required
def api_update_timeline(job_id: str):
    """Replace the application progress timeline and sync status / applied_at."""
    data = request.get_json(silent=True) or {}
    raw_timeline = data.get("timeline", [])
    unknown = sorted({
        str(item.get("step"))
        for item in (raw_timeline if isinstance(raw_timeline, list) else [])
        if isinstance(item, dict) and item.get("step") not in TIMELINE_STEPS
    })
    if unknown:
        # Never drop a step silently and still report success.
        return jsonify({
            "error": (
                f"Unknown progress step: {', '.join(unknown)}. "
                "If you just updated the app, restart the web server."
            ),
        }), 400
    timeline = _normalize_timeline(raw_timeline)

    try:
        oid = ObjectId(job_id)
    except Exception:
        return jsonify({"error": "Invalid job ID"}), 400

    collection = get_collection("matched_jobs")
    job = collection.find_one({"_id": oid})
    if not job:
        return jsonify({"error": "Job not found"}), 404

    now = datetime.now()
    update_fields = _timeline_update_fields(job, timeline, now)
    new_status = update_fields["status"]
    timeline = update_fields["application_timeline"]

    collection.update_one({"_id": oid}, {"$set": update_fields})

    serialized = [
        {
            "step": e["step"],
            "label": TIMELINE_STEPS[e["step"]],
            "at": _format_timeline_at(e["at"]),
        }
        for e in timeline
    ]
    return jsonify({"ok": True, "status": new_status, "application_timeline": serialized})


@app.route("/api/jobs/<job_id>/notes", methods=["PATCH"])
@login_required
def api_update_notes(job_id: str):
    """Update job notes."""
    data = request.get_json(silent=True) or {}
    notes = data.get("notes", "")

    collection = get_collection("matched_jobs")
    result = collection.update_one(
        {"_id": ObjectId(job_id)},
        {"$set": {"notes": notes, "updated_at": datetime.now()}}
    )

    if result.matched_count == 0:
        return jsonify({"error": "Job not found"}), 404

    return jsonify({"ok": True})


@app.route("/api/jobs/<job_id>/highlights", methods=["PATCH"])
@login_required
def api_update_highlights(job_id: str):
    """Replace manual highlight tags for a matched job (UI only; not AI special_match)."""
    data = request.get_json(silent=True) or {}
    raw = data.get("highlights", [])
    if not isinstance(raw, list):
        return jsonify({"error": "highlights must be a list"}), 400

    cleaned = []
    seen = set()
    for item in raw:
        tag = str(item).strip()
        if not tag:
            continue
        # Cap length so free-text tags stay readable on cards
        tag = tag[:40]
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(tag)
        if len(cleaned) >= 8:
            break

    try:
        oid = ObjectId(job_id)
    except Exception:
        return jsonify({"error": "Invalid job ID"}), 400

    collection = get_collection("matched_jobs")
    result = collection.update_one(
        {"_id": oid},
        {"$set": {"highlights": cleaned, "updated_at": datetime.now()}},
    )

    if result.matched_count == 0:
        return jsonify({"error": "Job not found"}), 404

    return jsonify({"ok": True, "highlights": cleaned})


@app.route("/api/jobs/<job_id>/application-qa", methods=["PATCH"])
@login_required
def api_update_application_qa(job_id: str):
    """Update application Q&A pairs for a matched job."""
    data = request.get_json(silent=True) or {}
    raw_qa = data.get("application_qa", [])
    if not isinstance(raw_qa, list):
        return jsonify({"error": "application_qa must be a list"}), 400

    cleaned = []
    for item in raw_qa:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        answer = str(item.get("answer", "")).strip()
        if not question and not answer:
            continue
        cleaned.append({"question": question, "answer": answer})

    try:
        oid = ObjectId(job_id)
    except Exception:
        return jsonify({"error": "Invalid job ID"}), 400

    collection = get_collection("matched_jobs")
    result = collection.update_one(
        {"_id": oid},
        {"$set": {"application_qa": cleaned, "updated_at": datetime.now()}},
    )

    if result.matched_count == 0:
        return jsonify({"error": "Job not found"}), 404

    return jsonify({"ok": True, "application_qa": cleaned})


@app.route("/api/jobs/<job_id>/info", methods=["PATCH"])
@login_required
def api_update_job_info(job_id: str):
    """Update job title / company / location in matched_jobs."""
    data = request.get_json(silent=True) or {}
    update_fields = {}
    for field in ("title", "company", "location"):
        if field in data and isinstance(data[field], str):
            update_fields[field] = data[field].strip()

    if not update_fields:
        return jsonify({"error": "No fields to update"}), 400

    try:
        oid = ObjectId(job_id)
    except Exception:
        return jsonify({"error": "Invalid job ID"}), 400

    update_fields["updated_at"] = datetime.now()
    collection = get_collection("matched_jobs")
    result = collection.update_one({"_id": oid}, {"$set": update_fields})

    if result.matched_count == 0:
        return jsonify({"error": "Job not found"}), 404

    return jsonify({"ok": True})


@app.route("/api/jobs/<job_id>", methods=["GET"])
@login_required
def api_get_job(job_id: str):
    """Return one matched job (list projection) for deep-link open on Tracker."""
    try:
        oid = ObjectId(job_id)
    except Exception:
        return jsonify({"error": "Invalid job ID"}), 400

    job = get_collection("matched_jobs").find_one({"_id": oid}, JOBS_LIST_PROJECTION)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(_serialize(job))


@app.route("/api/jobs/<job_id>", methods=["DELETE"])
@login_required
def api_delete_job(job_id: str):
    """
    Permanently delete a matched job from the database.

    Removes the matched_jobs document and the corresponding jobs document
    (same job_id + source) so it will not reappear in Tracker or unmatched.
    """
    try:
        oid = ObjectId(job_id)
    except Exception:
        return jsonify({"error": "Invalid job ID"}), 400

    matched_col = get_collection("matched_jobs")
    job = matched_col.find_one({"_id": oid})
    if not job:
        return jsonify({"error": "Job not found"}), 404

    jid = job.get("job_id") or ""
    source = job.get("source") or ""

    matched_col.delete_one({"_id": oid})

    jobs_deleted = 0
    if jid and source:
        jobs_col = get_collection("jobs")
        if source == "indeed":
            variants = indeed_job_id_variants(jid) or [jid]
            result = jobs_col.delete_many({"job_id": {"$in": variants}, "source": "indeed"})
        else:
            result = jobs_col.delete_many({"job_id": jid, "source": source})
        jobs_deleted = int(result.deleted_count)

    return jsonify({
        "ok": True,
        "deleted_matched": 1,
        "deleted_jobs": jobs_deleted,
        "title": job.get("title") or "",
        "company": job.get("company") or "",
    })


@app.route("/api/unmatched-jobs/<job_id>/status", methods=["PATCH"])
@login_required
def api_update_unmatched_status(job_id: str):
    """Update user_status of an unmatched job (any score below threshold).
    When marked as 'watchlist' (Can Apply), also copy the job to matched_jobs
    so it can be tracked / marked applied on the Tracker page.
    """
    data = request.get_json(silent=True) or {}
    new_status = data.get("user_status", "")
    if new_status not in UNMATCHED_USER_STATUS_MAP:
        return jsonify({"error": f"Invalid user_status. Allowed: {list(UNMATCHED_USER_STATUS_MAP.keys())}"}), 400

    jobs_col = get_collection("jobs")
    result = jobs_col.update_one(
        {"_id": ObjectId(job_id)},
        {"$set": {"user_status": new_status, "updated_at": datetime.now()}}
    )

    if result.matched_count == 0:
        return jsonify({"error": "Job not found"}), 404

    # When marked Can Apply → copy to matched_jobs so it shows in the tracker
    copied_to_matched = False
    copy_error = None
    if new_status == "watchlist":
        try:
            job = jobs_col.find_one({"_id": ObjectId(job_id)})
            if not job:
                copy_error = "Source job not found after update"
            else:
                matched_col = get_collection("matched_jobs")
                jid    = job.get("job_id") or ""
                source = job.get("source") or ""
                now    = datetime.now()

                doc = {
                    "title":       job.get("title", ""),
                    "company":     job.get("company", ""),
                    "location":    job.get("location", ""),
                    "link":        job.get("link", ""),
                    "job_id":      jid,
                    "source":      source,
                    "description": job.get("description", ""),
                    "applicants":  job.get("applicants", ""),
                    "match_score": job.get("match_score", 0),
                    "recommendation":          job.get("recommendation", ""),
                    "special_match":           bool(job.get("special_match")),
                    "special_match_reasons":   job.get("special_match_reasons") or [],
                    "disqualification_reason": job.get("disqualification_reason", ""),
                    "match_reasons":           job.get("match_reasons", []),
                    "missing_requirements":    job.get("missing_requirements", []),
                    "red_flags":               job.get("red_flags", []),
                    "nice_to_have_matches":    job.get("nice_to_have_matches", []),
                    "summary":                 job.get("summary", ""),
                    "what_youll_do":           job.get("what_youll_do", {"matched": [], "unmatched": []}),
                    "what_theyre_looking_for": job.get("what_theyre_looking_for", {"matched": [], "unmatched": []}),
                    "status":         "pending",
                    "matched_at":     now,   # use NOW so it appears at top of list
                    "applied_at":     None,
                    "application_timeline": [],
                    "notes":          "",
                    "application_qa": [],
                    "highlights":     [],
                    "created_at":     now,
                    "from_unmatched": True,
                }

                # Insert only if this job is not already on the Tracker.
                # Never replace an existing doc — that would wipe applied / notes.
                existing = matched_col.find_one({"job_id": jid, "source": source})
                if existing:
                    copied_to_matched = True
                    print(f"[Can Apply] Already in matched_jobs job_id={jid} source={source}")
                else:
                    matched_col.insert_one(doc)
                    copied_to_matched = True
                    print(f"[Can Apply] Copied job_id={jid} source={source} to matched_jobs")

        except Exception as e:
            copy_error = str(e)
            print(f"[Can Apply] ERROR copying to matched_jobs: {e}")

    return jsonify({"ok": True, "user_status": new_status,
                    "copied_to_matched": copied_to_matched,
                    "copy_error": copy_error})


def _serialize_timeout(job: dict) -> dict:
    job["_id"] = str(job["_id"])
    for key in ("created_at", "updated_at", "added_to_matched_at"):
        if key in job and isinstance(job[key], datetime):
            job[key] = job[key].strftime("%Y-%m-%d %H:%M")
    job["link"] = _normalize_link(job)
    job.setdefault("company", "")
    job.setdefault("location", "")
    job.setdefault("keyword", "")
    job.setdefault("timeout_count", 1)
    job.setdefault("review_status", "open")
    return job


@app.route("/api/timeout-jobs")
@login_required
def api_timeout_jobs():
    """Title-qualified cards whose JD panel timed out during scrape."""
    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    source = request.args.get("source") or "all"
    review_status = request.args.get("review_status") or "open"
    jobs, total = get_timeout_jobs(
        page=page,
        page_size=50,
        source=source,
        review_status=review_status,
    )
    page_size = 50
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    page = min(max(1, page), pages)
    return jsonify({
        "jobs": [_serialize_timeout(j) for j in jobs],
        "total": total,
        "page": page,
        "pages": pages,
    })


@app.route("/api/timeout-jobs/<doc_id>/match", methods=["POST"])
@login_required
def api_timeout_match(doc_id: str):
    """Promote a timeout card to matched_jobs as pending (Not Applied)."""
    try:
        oid = ObjectId(doc_id)
    except Exception:
        return jsonify({"error": "Invalid id"}), 400

    col = get_collection("timeout_jobs")
    doc = col.find_one({"_id": oid})
    if not doc:
        return jsonify({"error": "Timeout job not found"}), 404

    jid = doc.get("job_id") or ""
    source = doc.get("source") or ""
    matched_col = get_collection("matched_jobs")
    existing = matched_col.find_one({"job_id": jid, "source": source})
    now = datetime.now()

    if not existing:
        matched_col.insert_one({
            "title": doc.get("title", ""),
            "company": doc.get("company", ""),
            "location": doc.get("location", ""),
            "link": _normalize_link(doc),
            "job_id": jid,
            "source": source,
            "description": "",
            "applicants": "",
            "match_score": None,
            "recommendation": "",
            "special_match": False,
            "special_match_reasons": [],
            "disqualification_reason": "",
            "match_reasons": ["Manually added after scrape timeout"],
            "missing_requirements": [],
            "red_flags": [],
            "nice_to_have_matches": [],
            "summary": "",
            "what_youll_do": {"matched": [], "unmatched": []},
            "what_theyre_looking_for": {"matched": [], "unmatched": []},
            "status": "pending",
            "matched_at": now,
            "applied_at": None,
            "application_timeline": [],
            "notes": "",
            "application_qa": [],
            "highlights": [],
            "from_timeout": True,
        })
        save_job({
            "title": doc.get("title", ""),
            "company": doc.get("company", ""),
            "location": doc.get("location", ""),
            "link": _normalize_link(doc),
            "job_id": jid,
            "source": source,
            "status": "new",
            "applicants": "",
            "description": "",
            "description_empty": True,
        })
        get_collection("jobs").update_one(
            {"job_id": jid, "source": source},
            {"$set": {"matched_at": now, "from_timeout": True}},
        )

    col.update_one(
        {"_id": oid},
        {"$set": {
            "review_status": "added",
            "added_to_matched_at": now,
            "updated_at": now,
        }},
    )
    return jsonify({"ok": True, "already": bool(existing)})


@app.route("/api/timeout-jobs/<doc_id>/dismiss", methods=["POST"])
@login_required
def api_timeout_dismiss(doc_id: str):
    """Hide a timeout card from the review list."""
    try:
        oid = ObjectId(doc_id)
    except Exception:
        return jsonify({"error": "Invalid id"}), 400
    col = get_collection("timeout_jobs")
    result = col.update_one(
        {"_id": oid},
        {"$set": {"review_status": "dismissed", "updated_at": datetime.now()}},
    )
    if result.matched_count == 0:
        return jsonify({"error": "Timeout job not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/stats")
@login_required
def api_stats():
    """Return status counts for the summary bar and pipeline funnel."""
    collection = get_collection("matched_jobs")
    # Slim projection: progress chips use timeline / applied_at, not status alone
    docs = list(collection.find(
        {},
        {"_id": 0, "status": 1, "applied_at": 1, "application_timeline.step": 1},
    ))
    by_status = chip_status_counts(docs)
    ai_matched = len(docs)
    # Same as Journey + Locations: APPLICATION_QUERY (keeps side-status applies)
    applied_total = count_applied_applications()

    scraper = get_scraper_stats()
    unmatched = count_unmatched_jobs(threshold=MATCH_THRESHOLD)
    timeouts_open = count_open_timeout_jobs()

    return jsonify({
        # existing: matched-jobs filter chips
        "total":     ai_matched,
        "by_status": by_status,
        "unmatched": unmatched,
        "timeouts":  timeouts_open,
        # new: full-pipeline funnel
        "funnel": {
            "title_clicked": scraper.get("title_passed_clicked", 0),
            "german_filtered": scraper.get("german_filtered", 0),
            "ai_matched": ai_matched,
            "ai_unmatched": unmatched,
            "applied": applied_total,
        },
    })


@app.route("/api/stats/daily")
@login_required
def api_stats_daily():
    """Return per-day activity counts for the calendar heatmap."""
    try:
        days = int(request.args.get("days", 120))
    except (TypeError, ValueError):
        days = 120
    days = max(7, min(days, 366))
    return jsonify({"days": get_daily_activity_stats(days=days)})


@app.route("/api/stats/locations")
@login_required
def api_stats_locations():
    """
    Location mix for the same application set as Application Journey.

    Cities are normalized (street/postal lines fold into the city; "X near City"
    keeps X). Country-only / pure-remote labels count as Remote; a concrete city
    still wins over a (Remote) work-mode tag.
    """
    return jsonify(get_applied_location_stats())


@app.route("/api/journey")
@login_required
def api_journey():
    """
    Application Journey:
    - Overview line: first apply date → today + total applied count
    - Company tracks: only jobs with interview invite / rounds / offer,
      each showing the full step timeline (Applied → Invite → Rounds …)
    """
    collection = get_collection("matched_jobs")
    jobs = list(collection.find(APPLICATION_QUERY))

    total_applied = 0
    first_applied = None
    tracks = []

    for job in jobs:
        jid = str(job.get("_id"))
        company = job.get("company") or "Unknown"
        title = job.get("title") or ""
        raw_timeline = job.get("application_timeline") if isinstance(job.get("application_timeline"), list) else []

        steps = []
        for item in raw_timeline:
            if not isinstance(item, dict):
                continue
            step = item.get("step")
            if step not in JOURNEY_TRACK_STEPS:
                continue
            at = _parse_timeline_at(item.get("at"))
            if not at:
                continue
            steps.append({
                "step": step,
                "label": TIMELINE_STEPS.get(step, step),
                "at": _format_timeline_at(at),
                "date": at.strftime("%Y-%m-%d"),
                "_dt": at,
            })

        # Apply date for tracks (may exist even if status later changed)
        applied_at = resolve_applied_at(job)

        # Same set as funnel / Locations: APPLICATION_QUERY (includes side
        # statuses that still have applied_at or a timeline)
        total_applied += 1 + reapplied_count(job)
        if applied_at and (first_applied is None or applied_at < first_applied):
            first_applied = applied_at

        if applied_at and not any(s["step"] == "applied" for s in steps):
            steps.insert(0, {
                "step": "applied",
                "label": TIMELINE_STEPS["applied"],
                "at": _format_timeline_at(applied_at),
                "date": applied_at.strftime("%Y-%m-%d"),
                "_dt": applied_at,
            })

        # Backfill old flat interview/offer status without detailed timeline
        if not any(s["step"] in JOURNEY_INTERVIEW_STEPS for s in steps):
            if job.get("status") == "interview":
                at = _parse_timeline_at(job.get("updated_at") or applied_at) or datetime.now()
                steps.append({
                    "step": "interview_invite",
                    "label": TIMELINE_STEPS["interview_invite"],
                    "at": _format_timeline_at(at),
                    "date": at.strftime("%Y-%m-%d"),
                    "_dt": at,
                })
            elif job.get("status") == "offer":
                at = _parse_timeline_at(job.get("updated_at") or applied_at) or datetime.now()
                steps.append({
                    "step": "offer",
                    "label": TIMELINE_STEPS["offer"],
                    "at": _format_timeline_at(at),
                    "date": at.strftime("%Y-%m-%d"),
                    "_dt": at,
                })

        has_interview = any(s["step"] in JOURNEY_INTERVIEW_STEPS for s in steps)
        if not has_interview:
            continue

        steps.sort(key=lambda s: s["_dt"])
        for s in steps:
            s.pop("_dt", None)

        last_dt = _parse_timeline_at(steps[-1]["at"]) if steps else None
        tracks.append({
            "job_id": jid,
            "company": company,
            "title": title,
            "status": job.get("status") or "pending",
            "steps": steps,
            "_sort": last_dt or datetime.min,
        })

    tracks.sort(key=lambda t: t["_sort"], reverse=True)
    for t in tracks:
        t.pop("_sort", None)

    today_dt = datetime.now()
    today = today_dt.strftime("%Y-%m-%d")
    start_date = first_applied.strftime("%Y-%m-%d") if first_applied else today

    # Axis end = later of today and the latest interview/event date (so future rounds sit on the scale)
    end_dt = today_dt
    for t in tracks:
        for s in t.get("steps") or []:
            at = _parse_timeline_at(s.get("at") or s.get("date"))
            if at and at > end_dt:
                end_dt = at
    end_date = end_dt.strftime("%Y-%m-%d")

    duration_days = 0
    if first_applied:
        duration_days = (end_dt.date() - first_applied.date()).days

    return jsonify({
        "start_date": start_date,
        "end_date": end_date,
        "today": today,
        "duration_days": duration_days,
        "total_applied": total_applied,
        "tracks": tracks,
    })


@app.route("/api/dev/reload-token")
def api_dev_reload_token():
    """Return latest mtime of HTML templates so the browser can auto-refresh."""
    templates_dir = app.template_folder or ""
    latest = 0.0
    try:
        for name in os.listdir(templates_dir):
            if not name.endswith((".html", ".htm")):
                continue
            path = os.path.join(templates_dir, name)
            try:
                latest = max(latest, os.path.getmtime(path))
            except OSError:
                continue
    except OSError:
        pass
    return jsonify({"token": str(int(latest * 1000))})


# ─── Manual-apply endpoint ────────────────────────────────────────────────────

# Background task state (one run at a time)
_manual_apply_lock = threading.Lock()
_manual_apply_state: dict = {
    "running": False,
    "log": [],
    "done_count": 0,
    "fail_count": 0,
    "skip_count": 0,
}


@app.route("/manual-apply")
@login_required
def manual_apply_page():
    return render_template("manual_apply.html")


@app.route("/api/manual-apply/lookup", methods=["POST"])
@login_required
def api_manual_apply_lookup():
    """
    Resolve pasted job URL(s) against jobs / matched_jobs / timeout_jobs.

    Body: {"url": "..."} or {"urls": ["...", ...]}
    Returns one result per URL so Log Job can open an existing Tracker card.
    """
    data = request.get_json(silent=True) or {}
    urls = data.get("urls")
    if urls is None and data.get("url"):
        urls = [data.get("url")]
    if not isinstance(urls, list):
        return jsonify({"error": "urls must be a list (or pass url)"}), 400

    cleaned = [u.strip() for u in urls if isinstance(u, str) and u.strip()]
    if not cleaned:
        return jsonify({"error": "No valid URLs provided"}), 400

    from core.job_url import detect_source, normalize_pasted_job_url, resolve_job_id

    results = []
    for raw in cleaned:
        url = normalize_pasted_job_url(raw) or raw
        source = detect_source(url)
        job_id = resolve_job_id(url, source)
        hit = lookup_known_job(job_id, source) if job_id else None
        if hit:
            stage = hit.get("stage") or ""
            on_tracker = bool(hit.get("on_tracker"))
            open_path = None
            if stage == "tracker" or hit["collection"] == "matched_jobs":
                open_path = f"/?open={hit['_id']}"
            elif stage == "unmatched":
                # Only AI-scored below-threshold rows appear on /unmatched
                open_path = f"/unmatched?open={hit['_id']}"
            elif stage == "timeout" or hit["collection"] == "timeout_jobs":
                open_path = "/timeouts"
            # awaiting_match: scraped but not AI-scored — nowhere useful to open
            results.append({
                "url": url,
                "found": True,
                "collection": hit["collection"],
                "stage": stage,
                "on_tracker": on_tracker,
                "promotable": not on_tracker,
                "id": hit["_id"],
                "title": hit["title"],
                "company": hit["company"],
                "status": hit["status"],
                "source": hit["source"],
                "job_id": hit["job_id"],
                "match_score": hit.get("match_score"),
                "open_path": open_path,
                "ai_pending": bool(hit.get("ai_pending")),
                "details_missing": job_missing_details(hit) if on_tracker else False,
                "needs_scrape": tracker_needs_scrape(hit) if on_tracker else False,
            })
        else:
            results.append({
                "url": url,
                "found": False,
                "source": source,
                "job_id": job_id or "",
                "on_tracker": False,
                "promotable": True,
            })

    return jsonify({"results": results})


@app.route("/api/manual-apply", methods=["POST"])
@login_required
def api_manual_apply():
    """
    Accepts a JSON body:
      {
        "urls": [...],
        "status": "pending"|"applied",
        "applied_date": "YYYY-MM-DD",
        "mode": "scrape"|"link_only"   # default scrape
      }
    Runs the manual job logger in a background thread.
    Returns immediately with {"status": "started"} or an error.
    Default tracker status is pending (Not Applied).

    mode=link_only: save URL stubs with ai_pending (no Chrome / no AI).
    Optional recommended_by: claude | cursor | gemini | gpt.
    Optional title / company / location (user-typed; later scrape will not overwrite).
    """
    data = request.get_json(silent=True) or {}
    urls = data.get("urls", [])
    if not isinstance(urls, list):
        return jsonify({"error": "urls must be a list"}), 400
    from core.job_url import normalize_pasted_job_url

    urls = [
        normalize_pasted_job_url(u) or u.strip()
        for u in urls
        if isinstance(u, str) and u.strip()
    ]
    urls = [u for u in urls if u]
    if not urls:
        return jsonify({"error": "No valid URLs provided"}), 400

    mode = (data.get("mode") or "scrape").strip().lower()
    if mode not in ("scrape", "link_only"):
        return jsonify({"error": "mode must be 'scrape' or 'link_only'"}), 400

    # Tracker status: pending (Not Applied, default) or applied
    job_status = (data.get("status") or "pending").strip().lower()
    if job_status not in ("pending", "applied"):
        return jsonify({"error": "status must be 'pending' or 'applied'"}), 400

    recommended_by = normalize_recommended_by(data.get("recommended_by"))
    if data.get("recommended_by") not in (None, "", "none"):
        raw_rec = str(data.get("recommended_by") or "").strip().lower()
        if raw_rec and raw_rec not in RECOMMENDED_BY_AGENTS and raw_rec != "none":
            return jsonify({
                "error": "recommended_by must be none, claude, cursor, gemini, or gpt",
            }), 400

    identity = normalize_manual_identity(
        data.get("title"), data.get("company"), data.get("location")
    )

    # Parse optional applied_date (only meaningful when status=applied).
    # HTML <input type="date"> is day-only; strptime would yield midnight.
    # If the chosen day is today, stamp the real submit time (e.g. 14:40).
    applied_date = None
    raw_date = (data.get("applied_date") or "").strip()
    if raw_date:
        try:
            applied_date = datetime.strptime(raw_date, "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "Invalid applied_date format, expected YYYY-MM-DD"}), 400
        now = datetime.now()
        if applied_date.date() == now.date():
            applied_date = now

    busy = _chrome_busy_reason()
    if busy:
        return jsonify({"error": busy}), 429

    if not _manual_apply_lock.acquire(blocking=False):
        return jsonify({"error": "A manual job log run is already in progress. Please wait."}), 429

    def run():
        try:
            _manual_apply_state["running"] = True
            _manual_apply_state["log"] = []
            _manual_apply_state["done_count"] = 0
            _manual_apply_state["fail_count"] = 0
            _manual_apply_state["skip_count"] = 0

            # Link-only must not import Playwright / scrapers (Render has neither).
            from core.manual_log import save_link_only_urls as _save_link_only

            def _process_urls(*args, **kwargs):
                try:
                    from scrapers.manual_apply_scraper import process_urls as _scrape
                except ImportError as e:
                    print(f"⚠️  Scrape is unavailable ({e}). Saving link(s) only.")
                    _save_link_only(*args, **kwargs)
                    return
                _scrape(*args, **kwargs)

            # Monkey-patch print so progress goes to the state log
            import builtins
            _original_print = builtins.print

            def _capture_print(*args, **kwargs):
                line = " ".join(str(a) for a in args)
                _manual_apply_state["log"].append(line)
                _original_print(*args, **kwargs)

            builtins.print = _capture_print
            try:
                if mode == "link_only":
                    _save_link_only(
                        urls,
                        applied_date=applied_date,
                        status=job_status,
                        recommended_by=recommended_by,
                        identity=identity,
                    )
                else:
                    _process_urls(
                        urls,
                        applied_date=applied_date,
                        status=job_status,
                        recommended_by=recommended_by,
                        identity=identity,
                    )
                # Count results from log
                for line in _manual_apply_state["log"]:
                    if (
                        "✅ Saved to matched_jobs" in line
                        or "Marked existing Tracker job as applied" in line
                    ):
                        _manual_apply_state["done_count"] += 1
                    elif "Already on Tracker" in line and "skipping" in line:
                        _manual_apply_state["skip_count"] += 1
                    elif (
                        "❌ Scraping failed" in line
                        or "Cannot extract" in line
                        or "Cannot resolve job_id" in line
                        or "Cannot build a job record" in line
                        or "Fatal error" in line
                    ):
                        _manual_apply_state["fail_count"] += 1
            finally:
                builtins.print = _original_print
        except Exception as e:
            _manual_apply_state["log"].append(f"❌ Fatal error: {e}")
        finally:
            _manual_apply_state["running"] = False
            _manual_apply_lock.release()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return jsonify({
        "status": "started",
        "total": len(urls),
        "job_status": job_status,
        "mode": mode,
    })


@app.route("/api/manual-apply/status")
@login_required
def api_manual_apply_status():
    """Poll this to get progress of the current / last manual-apply run."""
    state = _manual_apply_state
    return jsonify({
        "running":    state["running"],
        "log":        state["log"][-100:],   # last 100 lines
        "done_count": state["done_count"],
        "fail_count": state["fail_count"],
        "skip_count": state.get("skip_count", 0),
    })


# ─── Per-card Run AI (scrape + match existing Tracker row) ────────────────────

_run_ai_lock = threading.Lock()
_run_ai_state: dict = {
    "running": False,
    "job_id": None,
    "log": [],
    "ok": False,
    "error": None,
    "match_score": None,
    "match_gate": None,
    "gate_reason": None,
}

_fetch_batch_lock = threading.Lock()
_fetch_batch_state: dict = {
    "running": False,
    "mode": "fetch",
    "total": 0,
    "current": 0,
    "done_count": 0,
    "fail_count": 0,
    "skip_count": 0,
    "results": [],
    "error": None,
}
BATCH_BUSY_MSG = "A batch Fetch details / Run AI is already running."


def _chrome_busy_reason(*, ignore_run_ai: bool = False) -> Optional[str]:
    """Why a Chrome scrape cannot start now, or None if idle."""
    if _fetch_batch_state.get("running"):
        return BATCH_BUSY_MSG
    if not ignore_run_ai and _run_ai_state.get("running"):
        return "Run AI is already in progress. Please wait."
    if _manual_apply_state.get("running"):
        return "Manual Log Job is running — wait for it to finish."
    return None


def guard_fetch_details_playwright() -> tuple:
    """
    Check Playwright once for Fetch details.

    Returns (error_payload, http_status) when scrape cannot run on this
    host (Render), otherwise (None, None).
    """
    try:
        from scrapers.manual_apply_scraper import _load_playwright
        _load_playwright()
    except ImportError:
        return {"ok": False, "error": PLAYWRIGHT_UNAVAILABLE_MSG}, 503
    except Exception as e:
        err = str(e)
        if "playwright" in err.lower():
            return {"ok": False, "error": PLAYWRIGHT_UNAVAILABLE_MSG}, 503
        return {"ok": False, "error": err}, 500
    return None, None


def run_fetch_details_batch_jobs(docs: list, fetch_one=None) -> dict:
    """
    Sequentially fetch details for Tracker docs. One failure does not stop
    the rest. fetch_one(doc) -> (payload, http_status); default is
    run_fetch_job_details. Updates _fetch_batch_state as it goes.
    """
    fetch_one = fetch_one or run_fetch_job_details
    results = []
    done = 0
    failed = 0
    skipped = 0
    total = len(docs)
    _fetch_batch_state["total"] = total
    _fetch_batch_state["results"] = results
    _fetch_batch_state["done_count"] = 0
    _fetch_batch_state["fail_count"] = 0
    _fetch_batch_state["skip_count"] = 0
    _fetch_batch_state["error"] = None

    for i, doc in enumerate(docs, 1):
        _fetch_batch_state["current"] = i
        title = doc.get("title") or doc.get("job_id") or "job"
        job_key = str(doc.get("_id") or doc.get("job_id") or i)
        if not can_fetch_details(doc):
            skipped += 1
            entry = {
                "id": job_key,
                "title": title,
                "ok": False,
                "error": "This card already has job details",
            }
            results.append(entry)
            _fetch_batch_state["skip_count"] = skipped
            _fetch_batch_state["fail_count"] = failed
            continue
        try:
            out = fetch_one(doc)
            if isinstance(out, tuple) and len(out) >= 1:
                payload = out[0]
            else:
                payload = out
            if not isinstance(payload, dict):
                payload = {"ok": False, "error": str(payload)}
        except Exception as e:
            payload = {"ok": False, "error": str(e)}
        ok = bool(payload.get("ok"))
        err = None if ok else (payload.get("error") or "Fetch failed")
        if ok:
            done += 1
            title = payload.get("title") or title
        else:
            failed += 1
        results.append({
            "id": job_key,
            "title": title,
            "ok": ok,
            "error": err,
        })
        _fetch_batch_state["done_count"] = done
        _fetch_batch_state["fail_count"] = failed
        _fetch_batch_state["skip_count"] = skipped

    return {
        "ok": failed == 0 and skipped == 0,
        "total": total,
        "done_count": done,
        "fail_count": failed,
        "skip_count": skipped,
        "results": results,
        "error": None,
        "mode": "fetch",
    }


def run_ai_on_tracker_job(doc: dict) -> tuple:
    """
    Scrape JD (if needed) and run AI matching on one Tracker card.

    Lazy-imports the scraper so Render never crashes at import time.
    Returns (payload, http_status).
    """
    try:
        from scrapers.manual_apply_scraper import run_ai_on_matched_job
    except ImportError:
        return {"ok": False, "error": PLAYWRIGHT_UNAVAILABLE_MSG}, 503
    try:
        result = run_ai_on_matched_job(doc)
    except ImportError:
        return {"ok": False, "error": PLAYWRIGHT_UNAVAILABLE_MSG}, 503
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500
    if result.get("ok"):
        return result, 200
    err = result.get("error") or "Run AI failed"
    code = 503 if PLAYWRIGHT_UNAVAILABLE_MSG in err else 502
    return {**result, "error": err}, code


def run_ai_batch_jobs(docs: list, run_one=None) -> dict:
    """
    Sequentially Run AI (scrape + match) for Tracker docs. One failure
    does not stop the rest. run_one(doc) -> (payload, http_status).
    Updates _fetch_batch_state as it goes.
    """
    run_one = run_one or run_ai_on_tracker_job
    results = []
    done = 0
    failed = 0
    skipped = 0
    total = len(docs)
    _fetch_batch_state["mode"] = "run_ai"
    _fetch_batch_state["total"] = total
    _fetch_batch_state["results"] = results
    _fetch_batch_state["done_count"] = 0
    _fetch_batch_state["fail_count"] = 0
    _fetch_batch_state["skip_count"] = 0
    _fetch_batch_state["error"] = None

    for i, doc in enumerate(docs, 1):
        _fetch_batch_state["current"] = i
        title = doc.get("title") or doc.get("job_id") or "job"
        job_key = str(doc.get("_id") or doc.get("job_id") or i)
        try:
            out = run_one(doc)
            if isinstance(out, tuple) and len(out) >= 1:
                payload = out[0]
            else:
                payload = out
            if not isinstance(payload, dict):
                payload = {"ok": False, "error": str(payload)}
        except Exception as e:
            payload = {"ok": False, "error": str(e)}
        ok = bool(payload.get("ok"))
        err = None if ok else (payload.get("error") or "Run AI failed")
        if ok:
            done += 1
            title = payload.get("title") or title
        else:
            failed += 1
        results.append({
            "id": job_key,
            "title": title,
            "ok": ok,
            "error": err,
            "match_score": payload.get("match_score"),
            "match_gate": payload.get("match_gate") or "",
            "used_stored_jd": bool(payload.get("used_stored_jd")),
        })
        _fetch_batch_state["done_count"] = done
        _fetch_batch_state["fail_count"] = failed
        _fetch_batch_state["skip_count"] = skipped

    return {
        "ok": failed == 0,
        "total": total,
        "done_count": done,
        "fail_count": failed,
        "skip_count": skipped,
        "results": results,
        "error": None,
        "mode": "run_ai",
    }


def parse_batch_job_oids(raw_ids) -> tuple:
    """
    Parse POST ids into ObjectIds.

    Returns (oids, None) or (None, (error_payload, http_status)).
    """
    if not isinstance(raw_ids, list) or not raw_ids:
        return None, ({"ok": False, "error": "Select at least one job"}, 400)
    oids = []
    seen = set()
    for raw in raw_ids:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        try:
            oids.append(ObjectId(text))
        except Exception:
            return None, ({"ok": False, "error": f"Invalid id: {text}"}, 400)
        seen.add(text)
        if len(oids) >= FETCH_DETAILS_BATCH_MAX:
            break
    if not oids:
        return None, ({"ok": False, "error": "Select at least one job"}, 400)
    return oids, None


def load_tracker_docs_in_order(oids: list) -> tuple:
    """Return (docs, missing_id_strings) in the requested order."""
    matched_col = get_collection("matched_jobs")
    docs_by_id = {
        doc["_id"]: doc
        for doc in matched_col.find({"_id": {"$in": oids}})
    }
    docs = []
    missing = []
    for oid in oids:
        doc = docs_by_id.get(oid)
        if not doc:
            missing.append(str(oid))
            continue
        docs.append(doc)
    return docs, missing


def start_chrome_batch(docs: list, *, runner, mode: str, missing: list, raw_count: int, oid_count: int):
    """Start a sequential Chrome batch on the shared fetch/run-ai lock."""
    if not _fetch_batch_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": BATCH_BUSY_MSG}), 429

    def run():
        try:
            _fetch_batch_state["running"] = True
            _fetch_batch_state["mode"] = mode
            _fetch_batch_state["current"] = 0
            _fetch_batch_state["error"] = None
            runner(docs)
        except Exception as e:
            _fetch_batch_state["error"] = str(e)
            _fetch_batch_state["results"].append({
                "id": "",
                "title": "batch",
                "ok": False,
                "error": str(e),
            })
            _fetch_batch_state["fail_count"] = _fetch_batch_state.get("fail_count", 0) + 1
        finally:
            _fetch_batch_state["running"] = False
            _fetch_batch_lock.release()

    _fetch_batch_state["running"] = True
    _fetch_batch_state["mode"] = mode
    _fetch_batch_state["total"] = len(docs)
    _fetch_batch_state["current"] = 0
    _fetch_batch_state["done_count"] = 0
    _fetch_batch_state["fail_count"] = 0
    _fetch_batch_state["skip_count"] = 0
    _fetch_batch_state["results"] = []
    _fetch_batch_state["error"] = None
    threading.Thread(target=run, daemon=True).start()
    return jsonify({
        "ok": True,
        "status": "started",
        "mode": mode,
        "total": len(docs),
        "skipped_missing": missing,
        "truncated": raw_count > oid_count,
        "limit": FETCH_DETAILS_BATCH_MAX,
    })


def _missing_details_mongo_clause() -> dict:
    """Link-only / empty-JD stubs, including jobs logged before recommended_by."""
    return {
        "$or": [
            {"title": {"$regex": r"^\(Link only\)"}},
            {"ai_pending": True, "details_fetched_at": {"$exists": False}},
            {
                "details_fetched_at": {"$exists": False},
                "$or": [
                    {"summary": {"$exists": False}},
                    {"summary": {"$in": [None, ""]}},
                ],
            },
        ]
    }


def collect_fetch_detail_targets(query: dict, *, missing_only: bool = False, limit: int = FETCH_DETAILS_BATCH_MAX) -> list:
    """Tracker rows that can Fetch details, matching the list filters, newest first."""
    collection = get_collection("matched_jobs")
    missing_clause = _missing_details_mongo_clause()
    fetch_clause = (
        missing_clause
        if missing_only
        else {
            "$or": [
                {"recommended_by": {"$in": list(RECOMMENDED_BY_AGENTS)}},
                missing_clause,
            ]
        }
    )
    full_query = combine_job_list_query([query, fetch_clause])
    cap = max(1, min(int(limit or FETCH_DETAILS_BATCH_MAX), FETCH_DETAILS_BATCH_MAX))
    docs = list(
        collection.find(full_query, JOBS_LIST_PROJECTION)
        .sort("matched_at", -1)
        .limit(cap * 3)
    )
    out = []
    for doc in docs:
        serialized = _serialize(dict(doc))
        if missing_only and not serialized.get("details_missing"):
            continue
        out.append({
            "id": serialized["_id"],
            "title": serialized.get("title") or "",
            "details_missing": bool(serialized.get("details_missing")),
        })
        if len(out) >= cap:
            break
    return out


@app.route("/api/jobs/<job_id>/run-ai", methods=["POST"])
@login_required
def api_job_run_ai(job_id: str):
    """
    Run AI matching on a Tracker card and update analysis fields (preserves
    notes / timeline / status). A complete stored JD is used as-is; otherwise
    the link is scraped via CDP first (requires debug Chrome).
    """
    try:
        oid = ObjectId(job_id)
    except Exception:
        return jsonify({"error": "Invalid id"}), 400

    matched_col = get_collection("matched_jobs")
    doc = matched_col.find_one({"_id": oid})
    if not doc:
        return jsonify({"error": "Job not found"}), 404

    if not _run_ai_lock.acquire(blocking=False):
        return jsonify({
            "error": "Another Run AI is already in progress. Please wait.",
            "busy_job_id": _run_ai_state.get("job_id"),
        }), 429

    # A complete stored JD is matched without Chrome; only a scrape needs
    # Chrome to be free of Log Job / batch Fetch details.
    try:
        from scrapers.manual_apply_scraper import stored_jd_usable
        uses_stored_jd = stored_jd_usable(doc)
    except ImportError:
        uses_stored_jd = False
    if not uses_stored_jd:
        busy = _chrome_busy_reason(ignore_run_ai=True)
        if busy:
            _run_ai_lock.release()
            return jsonify({"error": busy}), 429

    def run():
        try:
            _run_ai_state["running"] = True
            _run_ai_state["job_id"] = job_id
            _run_ai_state["log"] = []
            _run_ai_state["ok"] = False
            _run_ai_state["error"] = None
            _run_ai_state["match_score"] = None
            _run_ai_state["match_gate"] = None
            _run_ai_state["gate_reason"] = None

            from scrapers.manual_apply_scraper import run_ai_on_matched_job

            import builtins
            _original_print = builtins.print

            def _capture_print(*args, **kwargs):
                line = " ".join(str(a) for a in args)
                _run_ai_state["log"].append(line)
                _original_print(*args, **kwargs)

            builtins.print = _capture_print
            try:
                result = run_ai_on_matched_job(doc)
                _run_ai_state["ok"] = bool(result.get("ok"))
                _run_ai_state["error"] = result.get("error")
                _run_ai_state["match_score"] = result.get("match_score")
                _run_ai_state["match_gate"] = result.get("match_gate")
                _run_ai_state["gate_reason"] = result.get("gate_reason")
            finally:
                builtins.print = _original_print
        except Exception as e:
            _run_ai_state["ok"] = False
            _run_ai_state["error"] = str(e)
            _run_ai_state["log"].append(f"❌ Fatal error: {e}")
        finally:
            _run_ai_state["running"] = False
            _run_ai_lock.release()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return jsonify({
        "status": "started",
        "job_id": job_id,
        "uses_stored_jd": uses_stored_jd,
    })


def run_fetch_job_details(doc: dict) -> tuple:
    """
    Fetch scraped job fields for a Tracker card that still needs a JD
    (or is agent-tagged). Lazy-imports the scraper so Render never
    crashes at import time. Returns (payload, http_status).
    """
    if not can_fetch_details(doc):
        return {
            "ok": False,
            "error": "This card already has job details",
        }, 400
    try:
        from scrapers.manual_apply_scraper import fetch_job_details_for_matched_job
    except ImportError:
        return {"ok": False, "error": PLAYWRIGHT_UNAVAILABLE_MSG}, 503
    try:
        result = fetch_job_details_for_matched_job(doc)
    except ImportError:
        return {"ok": False, "error": PLAYWRIGHT_UNAVAILABLE_MSG}, 503
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500
    if result.get("ok"):
        return result, 200
    err = result.get("error") or "Fetch failed"
    code = 503 if PLAYWRIGHT_UNAVAILABLE_MSG in err else 502
    return {**result, "error": err}, code


@app.route("/api/jobs/<job_id>/fetch-details", methods=["POST"])
@login_required
def api_job_fetch_details(job_id: str):
    """
    Scrape title/company/location/description for a missing-details (or
    agent-tagged) Tracker job. Does not run AI. Requires debug Chrome
    locally; on Render returns a friendly Playwright-missing error.
    """
    try:
        oid = ObjectId(job_id)
    except Exception:
        return jsonify({"error": "Invalid id"}), 400

    doc = get_collection("matched_jobs").find_one({"_id": oid})
    if not doc:
        return jsonify({"error": "Job not found"}), 404

    busy = _chrome_busy_reason()
    if busy:
        return jsonify({"ok": False, "error": busy}), 429

    payload, status = run_fetch_job_details(doc)
    return jsonify(payload), status


@app.route("/api/fetch-details/ids")
@login_required
def api_fetch_details_ids():
    """
    IDs of Tracker cards that can Fetch details for the current list filters.

    ?missing=1 limits to link-only / empty-JD stubs (including jobs
    logged before recommended_by existed). Used by Select missing details.
    """
    missing = (request.args.get("missing") or "").strip().lower() in ("1", "true", "yes")
    query = _jobs_list_query_from_args(request.args)
    targets = collect_fetch_detail_targets(query, missing_only=missing)
    return jsonify({
        "ids": [t["id"] for t in targets],
        "jobs": targets,
        "total": len(targets),
        "missing_only": missing,
        "limit": FETCH_DETAILS_BATCH_MAX,
    })


@app.route("/api/fetch-details/batch", methods=["POST"])
@login_required
def api_fetch_details_batch():
    """
    Fetch details for many Tracker cards, sequentially (no AI).

    Playwright is checked once up front so Render returns the friendly
    error immediately instead of failing per job. One scrape failure
    does not stop the rest. Poll GET /api/fetch-details/batch/status.
    """
    data = request.get_json(silent=True) or {}
    raw_ids = data.get("ids") or []
    oids, err = parse_batch_job_oids(raw_ids)
    if err:
        payload, status = err
        return jsonify(payload), status

    busy = _chrome_busy_reason()
    if busy:
        return jsonify({"ok": False, "error": busy}), 429

    guard, guard_status = guard_fetch_details_playwright()
    if guard:
        return jsonify(guard), guard_status

    docs, missing = load_tracker_docs_in_order(oids)
    if not docs:
        return jsonify({"ok": False, "error": "No matching Tracker jobs"}), 404

    return start_chrome_batch(
        docs,
        runner=run_fetch_details_batch_jobs,
        mode="fetch",
        missing=missing,
        raw_count=len(raw_ids),
        oid_count=len(oids),
    )


@app.route("/api/run-ai/batch", methods=["POST"])
@login_required
def api_run_ai_batch():
    """
    Scrape + AI-match many Tracker cards, sequentially.

    Same Chrome lock as batch Fetch details. Playwright is checked once
    up front (Render-safe). One failure does not stop the rest.
    Poll GET /api/fetch-details/batch/status (shared progress).
    """
    data = request.get_json(silent=True) or {}
    raw_ids = data.get("ids") or []
    oids, err = parse_batch_job_oids(raw_ids)
    if err:
        payload, status = err
        return jsonify(payload), status

    busy = _chrome_busy_reason()
    if busy:
        return jsonify({"ok": False, "error": busy}), 429

    guard, guard_status = guard_fetch_details_playwright()
    if guard:
        return jsonify(guard), guard_status

    docs, missing = load_tracker_docs_in_order(oids)
    if not docs:
        return jsonify({"ok": False, "error": "No matching Tracker jobs"}), 404

    return start_chrome_batch(
        docs,
        runner=run_ai_batch_jobs,
        mode="run_ai",
        missing=missing,
        raw_count=len(raw_ids),
        oid_count=len(oids),
    )


@app.route("/api/fetch-details/batch/status")
@login_required
def api_fetch_details_batch_status():
    """Poll progress for the current / last batch Fetch details or Run AI."""
    state = _fetch_batch_state
    return jsonify({
        "running": state["running"],
        "mode": state.get("mode") or "fetch",
        "total": state.get("total") or 0,
        "current": state.get("current") or 0,
        "done_count": state.get("done_count") or 0,
        "fail_count": state.get("fail_count") or 0,
        "skip_count": state.get("skip_count") or 0,
        "results": state.get("results") or [],
        "error": state.get("error"),
    })


@app.route("/api/jobs/<job_id>/run-ai/status")
@login_required
def api_job_run_ai_status(job_id: str):
    """Poll progress for the current / last Run AI job."""
    state = _run_ai_state
    return jsonify({
        "running": state["running"] and state.get("job_id") == job_id,
        "job_id": state.get("job_id"),
        "log": state["log"][-80:],
        "ok": state["ok"] if state.get("job_id") == job_id and not state["running"] else None,
        "error": state.get("error") if state.get("job_id") == job_id else None,
        "match_score": state.get("match_score") if state.get("job_id") == job_id else None,
        "match_gate": state.get("match_gate") if state.get("job_id") == job_id else None,
        "gate_reason": state.get("gate_reason") if state.get("job_id") == job_id else None,
    })


@app.route("/api/jobs/<job_id>/check-gates", methods=["POST"])
@login_required
def api_job_check_gates(job_id: str):
    """
    Dry-run the hard gates (JD completeness, German, AI/ML title, tech
    stack) on the card's stored JD. No Chrome, no scoring, no DB writes —
    use Re-run AI afterwards to actually update the card.
    """
    try:
        oid = ObjectId(job_id)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid id"}), 400

    doc = get_collection("matched_jobs").find_one(
        {"_id": oid},
        {"title": 1, "company": 1, "location": 1, "description": 1,
         "source": 1, "match_gate": 1},
    )
    if not doc:
        return jsonify({"ok": False, "error": "Job not found"}), 404
    if not str(doc.get("description") or "").strip():
        return jsonify({
            "ok": False,
            "error": "No stored JD on this card — Fetch job details or Run AI first",
        }), 400

    try:
        from matching.gate_check import check_hard_gates
        result = check_hard_gates(doc)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Gate check failed: {e}"}), 500

    return jsonify({
        "ok": True,
        "current_gate": doc.get("match_gate") or "",
        **result,
    })


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Job Tracker Web UI")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", "-p", type=int, default=5050, help="Port to listen on")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    args = parser.parse_args()

    print(f"\n🌐 Job Tracker starting at http://{args.host}:{args.port}")
    print("   Press Ctrl+C to stop.\n")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
