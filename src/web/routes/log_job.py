"""Log Job (manual URLs) and the token-authenticated agent push endpoint."""
import hmac
import os
import threading
from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_login import login_required

from core.db_mongo import lookup_known_job
from core.manual_log import (
    AGENT_JOBS_MAX,
    RECOMMENDED_BY_AGENTS,
    job_missing_details,
    log_agent_jobs,
    normalize_manual_identity,
    normalize_recommended_by,
    tracker_needs_scrape,
)
from web.chrome_tasks import _chrome_busy_reason, _manual_apply_lock, _manual_apply_state

bp = Blueprint("log_job", __name__)


@bp.route("/api/manual-apply/lookup", methods=["POST"])
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

    from core.job_url import detect_source, expand_short_job_url, resolve_job_id

    results = []
    for raw in cleaned:
        url = expand_short_job_url(raw) or raw
        source = detect_source(url)
        job_id = resolve_job_id(url, source)
        hit = lookup_known_job(job_id, source, link=url) if job_id else None
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


@bp.route("/api/manual-apply", methods=["POST"])
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
    from core.job_url import expand_short_job_url

    urls = [
        expand_short_job_url(u) or u.strip()
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


@bp.route("/api/agent/jobs", methods=["POST"])
def api_agent_jobs():
    """
    Token-authenticated Log Job for scheduled agents (Claude daily job push).

    Header: Authorization: Bearer <AGENT_API_TOKEN>
    Body:   {"jobs": [{"url", "title"?, "company"?, "location"?,
                       "recommended_by"?, "reason"?, "country"?}, ...],
             "recommended_by": "claude"}   # optional, default claude
            (a bare job list is accepted as {"jobs": <list>})
    recommended_by is claude / cursor / gemini / gpt; a per-job value
    overrides the top-level one (each agent should send its own name).
    reason is the agent's one-line "why this job fits" (stored as
    recommend_reason, shown on the Tracker card).
    country is the job's country ("Finland", "FI") or "Remote" for a
    remote role open across countries; it overrides the location guess.
    Saves link-only Tracker stubs (pending, ai_pending) synchronously — no
    Chrome / no AI, so it runs on Render. A job_id already in matched_jobs /
    jobs / timeout_jobs is skipped with no write.
    Disabled (503) when AGENT_API_TOKEN is not set.
    """
    expected = os.getenv("AGENT_API_TOKEN", "").strip()
    if not expected:
        return jsonify({"error": "Agent API is disabled (AGENT_API_TOKEN not set)"}), 503
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token or not hmac.compare_digest(token, expected):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    # Agents sometimes post the bare job list instead of {"jobs": [...]}
    if isinstance(data, list):
        data = {"jobs": data}
    if not isinstance(data, dict):
        return jsonify({"error": "Body must be {\"jobs\": [...]} or a job list"}), 400
    jobs = data.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        return jsonify({"error": "jobs must be a non-empty list"}), 400
    if len(jobs) > AGENT_JOBS_MAX:
        return jsonify({"error": f"At most {AGENT_JOBS_MAX} jobs per request"}), 400

    rec_error = jsonify({
        "error": "recommended_by must be claude, cursor, gemini, or gpt",
    }), 400
    raw_rec = str(data.get("recommended_by") or "claude").strip().lower()
    if raw_rec not in RECOMMENDED_BY_AGENTS:
        return rec_error
    for item in jobs:
        if isinstance(item, dict) and item.get("recommended_by") \
                and not normalize_recommended_by(item["recommended_by"]):
            return rec_error

    result = log_agent_jobs(jobs, recommended_by=raw_rec)
    return jsonify({
        "recommended_by": raw_rec,
        "saved": len(result["ok"]),
        "skipped": len(result["skipped"]),
        "failed": len(result["failed"]),
        "urls": result,
    }), 200


@bp.route("/api/manual-apply/status")
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
