"""Unmatched page: AI-scored jobs below the match threshold."""
from datetime import datetime

from bson import ObjectId
from flask import Blueprint, jsonify, request
from flask_login import login_required

from core.db_mongo import get_collection, get_unmatched_jobs
from core.job_origin import found_origin
from web.serialize import _serialize
from web.status import MATCH_THRESHOLD

bp = Blueprint("unmatched", __name__)

UNMATCHED_PAGE_SIZE = 20


# Unmatched jobs user_status mapping (manual override of AI mismatch)
UNMATCHED_USER_STATUS_MAP = {
    "":           "Unmarked",
    "watchlist":  "Can Apply",
}


@bp.route("/api/unmatched-jobs")
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


@bp.route("/api/unmatched-jobs/<job_id>", methods=["GET"])
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


@bp.route("/api/unmatched-jobs/<job_id>/status", methods=["PATCH"])
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
                    "origin":         found_origin(job.get("source")),
                    **({"posted_at": job["posted_at"]} if job.get("posted_at") else {}),
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
