"""Timeouts page: title-qualified cards whose JD timed out during scrape."""
from datetime import datetime

from bson import ObjectId
from flask import Blueprint, jsonify, request
from flask_login import login_required

from core.db_mongo import get_collection, get_timeout_jobs, save_job
from core.job_origin import ORIGIN_PIPELINE
from web.serialize import _normalize_link

bp = Blueprint("timeouts", __name__)


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


@bp.route("/api/timeout-jobs")
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


@bp.route("/api/timeout-jobs/<doc_id>/match", methods=["POST"])
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
            "origin": ORIGIN_PIPELINE,
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


@bp.route("/api/timeout-jobs/<doc_id>/dismiss", methods=["POST"])
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
