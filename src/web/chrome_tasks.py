"""
Background Chrome work started from the Tracker: Log Job, Run AI, batch
Fetch details / Run AI.

Holds the in-memory state + locks for each task so routes can report
progress and refuse to start a second Chrome scrape. Scrapers are
lazy-imported so Render (no Playwright) never fails at import time.
"""
import threading
from typing import Optional

from bson import ObjectId
from flask import jsonify

from core.db_mongo import get_collection
from core.manual_log import (
    PLAYWRIGHT_UNAVAILABLE_MSG,
    RECOMMENDED_BY_AGENTS,
    can_fetch_details,
)
from web.list_query import JOBS_LIST_PROJECTION, combine_job_list_query
from web.serialize import _serialize

FETCH_DETAILS_BATCH_MAX = 80


# Background task state (one run at a time)
_manual_apply_lock = threading.Lock()
_manual_apply_state: dict = {
    "running": False,
    "log": [],
    "done_count": 0,
    "fail_count": 0,
    "skip_count": 0,
}


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


def _missing_ai_mongo_clause() -> dict:
    """Cards without an AI result yet (same rule as the card's Run AI hint)."""
    return {
        "$or": [
            {"ai_pending": True},
            {"match_score": {"$in": [None, ""]}},
            {"summary": {"$in": [None, ""]}},
        ]
    }


def collect_fetch_detail_targets(
    query: dict,
    *,
    missing_only: bool = False,
    missing_ai: bool = False,
    limit: int = FETCH_DETAILS_BATCH_MAX,
) -> list:
    """Tracker rows that can Fetch details / Run AI, matching the list filters, newest first."""
    collection = get_collection("matched_jobs")
    missing_clause = _missing_details_mongo_clause()
    if missing_ai:
        fetch_clause = _missing_ai_mongo_clause()
    else:
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
