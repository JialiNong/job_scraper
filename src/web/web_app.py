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
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from core.db_mongo import (
    get_collection,
    ensure_indexes,
    get_scraper_stats,
    get_daily_activity_stats,
    get_applied_location_stats,
    get_unmatched_jobs,
    count_unmatched_jobs,
    get_timeout_jobs,
    count_open_timeout_jobs,
    save_job,
    _parse_filter_date,
    APPLIED_STATUSES,
)
from core.scraper_utils import indeed_job_id_variants

load_dotenv()

app = Flask(
    __name__,
    template_folder=os.path.join(_PROJECT_ROOT, "templates"),
    static_folder=os.path.join(_PROJECT_ROOT, "static"),
)
# Always pick up HTML/template edits without restarting the server
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

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
}

# Other-dropdown statuses: no application progress, not counted as applied
SIDE_STATUSES = ("unsuitable", "closed", "repost")

# Application progress timeline steps (ordered interview track)
TIMELINE_STEPS = {
    "applied":         "Applied",
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
    if last == "applied":
        return "applied"
    if last.startswith("interview_"):
        return "interview"
    if last == "rejected":
        return "rejected"
    if last == "offer":
        return "offer"
    return fallback


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
    # Do not invent progress for side / reset statuses
    if status in (*SIDE_STATUSES, "pending"):
        return []
    # Backfill: older records only had a flat status (+ applied_at)
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
    for key in ("matched_at", "applied_at", "created_at", "updated_at", "description_cleared_at"):
        if key in job and isinstance(job[key], datetime):
            job[key] = job[key].strftime("%Y-%m-%d %H:%M")
    # Normalise job link (fixes relative LinkedIn URLs)
    job["link"] = _normalize_link(job)
    # Ensure notes field always exists
    job.setdefault("notes", "")
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

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/unmatched")
def unmatched():
    return render_template("unmatched.html")


@app.route("/timeouts")
def timeouts():
    return render_template("timeouts.html")


@app.route("/api/jobs")
def api_jobs():
    """Return all matched jobs, newest first."""
    collection = get_collection("matched_jobs")

    # Optional filters from query-string
    status_filter = request.args.get("status")      # e.g. ?status=pending
    source_filter = request.args.get("source")      # e.g. ?source=linkedin
    search_query  = request.args.get("q", "").strip()
    date_from     = request.args.get("date_from", "").strip() or None
    date_to       = request.args.get("date_to", "").strip() or None

    query: dict = {}
    if status_filter and status_filter != "all":
        query["status"] = status_filter
    if source_filter and source_filter != "all":
        query["source"] = source_filter
    # Filter by manual UI highlights (not AI special_match)
    highlights_filter = request.args.get("highlights", "").strip().lower()
    if highlights_filter in ("1", "true", "yes"):
        query["highlights.0"] = {"$exists": True}
    if search_query:
        query["$or"] = [
            {"title":   {"$regex": search_query, "$options": "i"}},
            {"company": {"$regex": search_query, "$options": "i"}},
        ]
    if date_from or date_to:
        time_filter: dict = {}
        start = _parse_filter_date(date_from)
        end   = _parse_filter_date(date_to, end_of_day=True)
        if start:
            time_filter["$gte"] = start
        if end:
            time_filter["$lte"] = end
        query["matched_at"] = time_filter

    jobs = list(
        collection.find(query, JOBS_LIST_PROJECTION)
        .sort("matched_at", -1)
        .limit(500)
    )
    jobs = [_serialize(j) for j in jobs]

    return jsonify({"jobs": jobs, "total": len(jobs)})


@app.route("/api/jobs/<job_id>/description")
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


@app.route("/api/jobs/<job_id>/status", methods=["PATCH"])
def api_update_status(job_id: str):
    """Update job status. Sets applied_at the first time a job is marked applied/rejected/interview."""
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
    update_fields = {"status": new_status, "updated_at": now}
    # Stamp applied_at once when entering any post-application status
    if new_status in APPLIED_STATUSES and not job.get("applied_at"):
        update_fields["applied_at"] = now

    # Side statuses (unsuitable/closed/repost/pending) clear progress; others keep timeline
    if new_status in (*SIDE_STATUSES, "pending"):
        update_fields["application_timeline"] = []
        if new_status == "pending":
            update_fields["applied_at"] = None

    collection.update_one({"_id": oid}, {"$set": update_fields})
    return jsonify({"ok": True, "status": new_status})


@app.route("/api/jobs/<job_id>/timeline", methods=["PATCH"])
def api_update_timeline(job_id: str):
    """Replace the application progress timeline and sync status / applied_at."""
    data = request.get_json(silent=True) or {}
    timeline = _normalize_timeline(data.get("timeline", []))

    try:
        oid = ObjectId(job_id)
    except Exception:
        return jsonify({"error": "Invalid job ID"}), 400

    collection = get_collection("matched_jobs")
    job = collection.find_one({"_id": oid})
    if not job:
        return jsonify({"error": "Job not found"}), 404

    now = datetime.now()
    prev_status = job.get("status", "pending")
    # Keep side statuses only when timeline is empty; otherwise derive from steps
    fallback = prev_status if prev_status in SIDE_STATUSES and not timeline else "pending"
    new_status = _status_from_timeline(timeline, fallback=fallback)

    update_fields = {
        "application_timeline": timeline,
        "status": new_status,
        "updated_at": now,
    }

    applied_event = next((e for e in timeline if e["step"] == "applied"), None)
    if applied_event:
        update_fields["applied_at"] = applied_event["at"]
    elif new_status not in APPLIED_STATUSES:
        update_fields["applied_at"] = None
    elif not job.get("applied_at") and timeline:
        update_fields["applied_at"] = timeline[0]["at"]

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


@app.route("/api/jobs/<job_id>", methods=["DELETE"])
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
def api_stats():
    """Return status counts for the summary bar and pipeline funnel."""
    collection = get_collection("matched_jobs")
    pipeline = [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]
    raw = list(collection.aggregate(pipeline))
    by_status = {item["_id"]: item["count"] for item in raw}
    ai_matched = sum(by_status.values())
    # Applied funnel step includes rejected + interview (all post-application)
    applied_total = sum(by_status.get(s, 0) for s in APPLIED_STATUSES)

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
def api_stats_daily():
    """Return per-day activity counts for the calendar heatmap."""
    try:
        days = int(request.args.get("days", 120))
    except (TypeError, ValueError):
        days = 120
    days = max(7, min(days, 366))
    return jsonify({"days": get_daily_activity_stats(days=days)})


@app.route("/api/stats/locations")
def api_stats_locations():
    """
    Location mix for applied-like jobs (applied / rejected / interview / offer).

    Cities are normalized (street/postal lines fold into the city; "X near City"
    keeps X). Country-only / pure-remote labels count as Remote; a concrete city
    still wins over a (Remote) work-mode tag.
    """
    return jsonify(get_applied_location_stats())


@app.route("/api/journey")
def api_journey():
    """
    Application Journey:
    - Overview line: first apply date → today + total applied count
    - Company tracks: only jobs with interview invite / rounds / offer,
      each showing the full step timeline (Applied → Invite → Rounds …)
    """
    collection = get_collection("matched_jobs")
    jobs = list(collection.find({
        "$or": [
            {"applied_at": {"$ne": None}},
            {"status": {"$in": list(APPLIED_STATUSES)}},
            {"application_timeline.0": {"$exists": True}},
        ]
    }))

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

        # Resolve applied date for overview count
        applied_at = next((s["_dt"] for s in steps if s["step"] == "applied"), None)
        if not applied_at:
            applied_at = _parse_timeline_at(job.get("applied_at"))
        if not applied_at and job.get("status") in APPLIED_STATUSES:
            applied_at = _parse_timeline_at(job.get("updated_at") or job.get("matched_at"))

        if applied_at:
            total_applied += 1
            if first_applied is None or applied_at < first_applied:
                first_applied = applied_at
            # Ensure Applied is on the track when we have a date
            if not any(s["step"] == "applied" for s in steps):
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
def manual_apply_page():
    return render_template("manual_apply.html")


@app.route("/api/manual-apply", methods=["POST"])
def api_manual_apply():
    """
    Accepts a JSON body:
      {"urls": [...], "status": "pending"|"applied", "applied_date": "YYYY-MM-DD"}
    Runs the manual job logger in a background thread.
    Returns immediately with {"status": "started"} or an error.
    Default tracker status is pending (Not Applied).
    """
    data = request.get_json(silent=True) or {}
    urls = data.get("urls", [])
    if not isinstance(urls, list):
        return jsonify({"error": "urls must be a list"}), 400
    urls = [u.strip() for u in urls if isinstance(u, str) and u.strip()]
    if not urls:
        return jsonify({"error": "No valid URLs provided"}), 400

    # Tracker status: pending (Not Applied, default) or applied
    job_status = (data.get("status") or "pending").strip().lower()
    if job_status not in ("pending", "applied"):
        return jsonify({"error": "status must be 'pending' or 'applied'"}), 400

    # Parse optional applied_date (only meaningful when status=applied)
    applied_date = None
    raw_date = (data.get("applied_date") or "").strip()
    if raw_date:
        try:
            applied_date = datetime.strptime(raw_date, "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "Invalid applied_date format, expected YYYY-MM-DD"}), 400

    if not _manual_apply_lock.acquire(blocking=False):
        return jsonify({"error": "A manual job log run is already in progress. Please wait."}), 429

    def run():
        try:
            _manual_apply_state["running"] = True
            _manual_apply_state["log"] = []
            _manual_apply_state["done_count"] = 0
            _manual_apply_state["fail_count"] = 0
            _manual_apply_state["skip_count"] = 0

            # Import here to avoid circular deps at module load
            from scrapers.manual_apply_scraper import process_urls as _process_urls

            # Monkey-patch print so progress goes to the state log
            import builtins
            _original_print = builtins.print

            def _capture_print(*args, **kwargs):
                line = " ".join(str(a) for a in args)
                _manual_apply_state["log"].append(line)
                _original_print(*args, **kwargs)

            builtins.print = _capture_print
            try:
                _process_urls(urls, applied_date=applied_date, status=job_status)
                # Count results from log
                for line in _manual_apply_state["log"]:
                    if "✅ Saved to matched_jobs" in line:
                        _manual_apply_state["done_count"] += 1
                    elif "Already in DB" in line and "skipping" in line:
                        _manual_apply_state["skip_count"] += 1
                    elif "❌ Scraping failed" in line or "Cannot extract" in line:
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
    return jsonify({"status": "started", "total": len(urls), "job_status": job_status})


@app.route("/api/manual-apply/status")
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
