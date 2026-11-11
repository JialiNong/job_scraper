"""Tracker cards (matched_jobs): list, read, edit, delete."""
from datetime import datetime

from bson import ObjectId
from flask import Blueprint, jsonify, request
from flask_login import login_required

from core.db_mongo import get_collection, lookup_known_job
from core.job_url import replacement_link_fields
from core.manual_log import can_fetch_details
from core.scraper_utils import indeed_job_id_variants
from web.chrome_tasks import _fetch_batch_state, parse_batch_job_oids
from web.list_query import JOBS_LIST_PROJECTION, _jobs_list_query_from_args
from web.serialize import _serialize, attach_company_history, parse_applicant_position
from web.status import (
    STATUS_MAP,
    TIMELINE_STEPS,
    _format_timeline_at,
    _normalize_timeline,
    _status_update_fields,
    _timeline_update_fields,
)

bp = Blueprint("jobs", __name__)


@bp.route("/api/jobs")
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
    jobs = attach_company_history([_serialize(j) for j in jobs], collection)

    return jsonify({
        "jobs": jobs,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    })


@bp.route("/api/jobs/<job_id>/description")
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


@bp.route("/api/jobs/<job_id>/status", methods=["PATCH"])
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


@bp.route("/api/jobs/<job_id>/timeline", methods=["PATCH"])
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


@bp.route("/api/jobs/<job_id>/notes", methods=["PATCH"])
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


@bp.route("/api/jobs/<job_id>/highlights", methods=["PATCH"])
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


@bp.route("/api/jobs/<job_id>/application-qa", methods=["PATCH"])
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


@bp.route("/api/jobs/<job_id>/applicant-position", methods=["PATCH"])
@login_required
def api_update_applicant_position(job_id: str):
    """Save 'I was applicant #N' (LinkedIn applicant count when you applied)."""
    data = request.get_json(silent=True) or {}
    value, error = parse_applicant_position(data.get("applicant_position"))
    if error:
        return jsonify({"error": error}), 400

    try:
        oid = ObjectId(job_id)
    except Exception:
        return jsonify({"error": "Invalid job ID"}), 400

    collection = get_collection("matched_jobs")
    result = collection.update_one(
        {"_id": oid},
        {"$set": {"applicant_position": value, "updated_at": datetime.now()}},
    )
    if result.matched_count == 0:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({"ok": True, "applicant_position": value})


@bp.route("/api/jobs/<job_id>/link", methods=["PATCH"])
@login_required
def api_replace_job_link(job_id: str):
    """
    Replace the posting URL on a Tracker card.

    Used when the original link is closed but the same (or a similar) role
    is still on the company page. Updates link / source / job_id / posting_key
    on matched_jobs and the twin jobs row so Fetch details can scrape the
    new page. Does not scrape here.
    """
    data = request.get_json(silent=True) or {}
    fields, error = replacement_link_fields(data.get("link") or data.get("url") or "")
    if error:
        return jsonify({"error": error}), 400

    try:
        oid = ObjectId(job_id)
    except Exception:
        return jsonify({"error": "Invalid job ID"}), 400

    matched_col = get_collection("matched_jobs")
    doc = matched_col.find_one({"_id": oid})
    if not doc:
        return jsonify({"error": "Job not found"}), 404

    same_link = (doc.get("link") or "").strip() == fields["link"]
    same_id = str(doc.get("job_id") or "") == fields["job_id"]
    same_source = str(doc.get("source") or "") == fields["source"]
    if same_link and same_id and same_source:
        return jsonify({
            "ok": True,
            "unchanged": True,
            "link": fields["link"],
            "source": fields["source"],
            "job_id": fields["job_id"],
        }), 200

    hit = lookup_known_job(fields["job_id"], fields["source"], link=fields["link"])
    if hit and hit.get("_id") and hit["_id"] != str(oid):
        title = (hit.get("title") or "another job").strip() or "another job"
        if hit.get("on_tracker"):
            msg = f"Already on Tracker as {title}"
        else:
            msg = f"This URL is already stored as another job ({title})"
        return jsonify({"error": msg}), 409

    now = datetime.utcnow()
    set_fields = {
        "link": fields["link"],
        "source": fields["source"],
        "job_id": fields["job_id"],
        "link_replaced_at": now,
        "updated_at": now,
    }
    update = {"$set": set_fields}
    if fields.get("posting_key"):
        set_fields["posting_key"] = fields["posting_key"]
    else:
        update["$unset"] = {"posting_key": ""}

    result = matched_col.update_one({"_id": oid}, update)
    if result.matched_count == 0:
        return jsonify({"error": "Job not found"}), 404

    old_job_id = doc.get("job_id") or ""
    old_source = doc.get("source") or ""
    if old_job_id and old_source:
        jobs_col = get_collection("jobs")
        if old_source == "indeed":
            variants = indeed_job_id_variants(old_job_id) or [old_job_id]
            jobs_query = {"job_id": {"$in": variants}, "source": "indeed"}
        else:
            jobs_query = {"job_id": old_job_id, "source": old_source}
        jobs_col.update_many(jobs_query, update)

    merged = {**doc, **set_fields}
    return jsonify({
        "ok": True,
        "unchanged": False,
        "link": fields["link"],
        "source": fields["source"],
        "job_id": fields["job_id"],
        "can_fetch_details": can_fetch_details(merged),
    })


@bp.route("/api/jobs/<job_id>/info", methods=["PATCH"])
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


@bp.route("/api/jobs/<job_id>", methods=["GET"])
@login_required
def api_get_job(job_id: str):
    """Return one matched job (list projection) for deep-link open on Tracker."""
    try:
        oid = ObjectId(job_id)
    except Exception:
        return jsonify({"error": "Invalid job ID"}), 400

    collection = get_collection("matched_jobs")
    job = collection.find_one({"_id": oid}, JOBS_LIST_PROJECTION)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(attach_company_history([_serialize(job)], collection)[0])


@bp.route("/api/jobs/<job_id>", methods=["DELETE"])
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

    deleted = delete_tracker_job(oid)
    if not deleted:
        return jsonify({"error": "Job not found"}), 404

    return jsonify({
        "ok": True,
        "deleted_matched": 1,
        "deleted_jobs": deleted["deleted_jobs"],
        "title": deleted["title"],
        "company": deleted["company"],
    })


def delete_tracker_job(oid) -> dict | None:
    """
    Delete one matched_jobs doc plus its jobs doc(s) (same job_id + source;
    Indeed: all jk variants). Returns None when the card does not exist.
    """
    matched_col = get_collection("matched_jobs")
    job = matched_col.find_one({"_id": oid}, {"job_id": 1, "source": 1, "title": 1, "company": 1})
    if not job:
        return None

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

    return {
        "deleted_jobs": jobs_deleted,
        "title": job.get("title") or "",
        "company": job.get("company") or "",
    }


@bp.route("/api/jobs/batch-delete", methods=["POST"])
@login_required
def api_batch_delete_jobs():
    """
    Permanently delete many Tracker cards (same as per-card Delete).

    Body: {"ids": [matched_jobs _id, ...]}. Refused while a batch Fetch
    details / Run AI is running so it cannot delete a card mid-scrape.
    """
    data = request.get_json(silent=True) or {}
    oids, err = parse_batch_job_oids(data.get("ids") or [])
    if err:
        payload, status = err
        return jsonify(payload), status
    if _fetch_batch_state.get("running"):
        return jsonify({
            "ok": False,
            "error": "Wait for the batch Fetch details / Run AI to finish",
        }), 409

    deleted_ids = []
    missing = 0
    for oid in oids:
        if delete_tracker_job(oid):
            deleted_ids.append(str(oid))
        else:
            missing += 1

    return jsonify({
        "ok": True,
        "deleted": len(deleted_ids),
        "missing": missing,
        "ids": deleted_ids,
    })
