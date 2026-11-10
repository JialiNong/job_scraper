"""Run AI and Fetch details: per card and in batches (Chrome, background)."""
import threading

from bson import ObjectId
from flask import Blueprint, jsonify, request
from flask_login import login_required

from core.db_mongo import get_collection
from web.chrome_tasks import (
    FETCH_DETAILS_BATCH_MAX,
    _chrome_busy_reason,
    _fetch_batch_state,
    _run_ai_lock,
    _run_ai_state,
    collect_fetch_detail_targets,
    guard_fetch_details_playwright,
    load_tracker_docs_in_order,
    parse_batch_job_oids,
    run_ai_batch_jobs,
    run_fetch_details_batch_jobs,
    run_fetch_job_details,
    start_chrome_batch,
)
from web.list_query import _jobs_list_query_from_args

bp = Blueprint("ai_tasks", __name__)


@bp.route("/api/jobs/<job_id>/run-ai", methods=["POST"])
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


@bp.route("/api/jobs/<job_id>/fetch-details", methods=["POST"])
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


@bp.route("/api/fetch-details/ids")
@login_required
def api_fetch_details_ids():
    """
    IDs of Tracker cards that can Fetch details for the current list filters.

    ?missing=1 limits to link-only / empty-JD stubs (including jobs
    logged before recommended_by existed). Used by Select missing details.
    ?missing_ai=1 limits to cards with no AI result yet (Select missing AI result).
    """
    missing = (request.args.get("missing") or "").strip().lower() in ("1", "true", "yes")
    missing_ai = (request.args.get("missing_ai") or "").strip().lower() in ("1", "true", "yes")
    query = _jobs_list_query_from_args(request.args)
    targets = collect_fetch_detail_targets(query, missing_only=missing, missing_ai=missing_ai)
    return jsonify({
        "ids": [t["id"] for t in targets],
        "jobs": targets,
        "total": len(targets),
        "missing_only": missing,
        "limit": FETCH_DETAILS_BATCH_MAX,
    })


@bp.route("/api/fetch-details/batch", methods=["POST"])
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


@bp.route("/api/run-ai/batch", methods=["POST"])
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


@bp.route("/api/fetch-details/batch/status")
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


@bp.route("/api/jobs/<job_id>/run-ai/status")
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
