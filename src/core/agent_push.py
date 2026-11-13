"""Agent job push: POST /api/agent/jobs and the local CLI twin.

New URLs land on the Tracker as link-only stubs. A URL already in the DB is
skipped with no overwrite of a scraped JD. Hits that live on Unmatched (or
are still awaiting a score) are tagged so the agent response, Unmatched
list, and daily Overview can show "an agent recommended this, the matcher
did not."
"""
from __future__ import annotations

from datetime import datetime

from bson import ObjectId

from core.db_mongo import COLLECTION_NAME, get_collection, lookup_known_job
from core.job_url import detect_source, expand_short_job_url, resolve_job_id
from core.manual_log import (
    normalize_manual_identity,
    normalize_recommend_reason,
    normalize_recommended_by,
    save_link_only_urls,
)

AGENT_JOBS_MAX = 50

# Stage → agent-facing skip bucket (Tracker vs Unmatched vs still in flight).
_STAGE_BUCKET = {
    "tracker": "already_on_tracker",
    "unmatched": "below_threshold",
    "awaiting_match": "awaiting_match",
    "timeout": "timeout",
}


def existing_from_hit(url: str, job_id: str, hit: dict) -> dict:
    """One skipped URL, with enough fields for an agent to explain the skip."""
    stage = hit.get("stage") or ""
    oid = hit.get("_id") or ""
    open_path = None
    if stage == "tracker" or hit.get("on_tracker"):
        open_path = f"/?open={oid}" if oid else "/"
    elif stage == "unmatched":
        open_path = f"/unmatched?open={oid}" if oid else "/unmatched"
    elif stage == "timeout":
        open_path = "/timeouts"
    score = hit.get("match_score")
    try:
        score = None if score is None else float(score)
    except (TypeError, ValueError):
        score = None
    return {
        "url": url,
        "job_id": hit.get("job_id") or job_id,
        "stage": stage,
        "on_tracker": bool(hit.get("on_tracker")),
        "title": hit.get("title") or "",
        "company": hit.get("company") or "",
        "match_score": score,
        "match_gate": hit.get("match_gate") or "",
        "open_path": open_path,
    }


def stamp_agent_recommendation(hit: dict, recommended_by, reason=None) -> bool:
    """
    Tag a `jobs` row that an agent also recommended.

    Does not copy the job to Tracker and does not touch the JD / score.
    Re-stamps the time so a later push still counts on today's heatmap.
    """
    if (hit or {}).get("collection") != COLLECTION_NAME:
        return False
    rec = normalize_recommended_by(recommended_by)
    if not rec:
        return False
    try:
        oid = ObjectId(str(hit.get("_id") or ""))
    except Exception:
        return False
    fields = {
        "agent_recommended_by": rec,
        "agent_recommended_at": datetime.now(),
    }
    blurb = normalize_recommend_reason(reason)
    if blurb:
        fields["agent_recommend_reason"] = blurb
    result = get_collection(COLLECTION_NAME).update_one(
        {"_id": oid},
        {"$set": fields},
    )
    return result.matched_count > 0


def _empty_result() -> dict:
    return {
        "ok": [],
        "skipped": [],
        "failed": [],
        "existing": [],
        "already_on_tracker": 0,
        "below_threshold": 0,
        "awaiting_match": 0,
        "timeout": 0,
    }


def _count_skip_buckets(existing: list) -> dict:
    counts = {
        "already_on_tracker": 0,
        "below_threshold": 0,
        "awaiting_match": 0,
        "timeout": 0,
    }
    for item in existing:
        bucket = _STAGE_BUCKET.get(item.get("stage") or "")
        if bucket:
            counts[bucket] += 1
    return counts


def log_agent_jobs(jobs: list, recommended_by="claude") -> dict:
    """
    Batch-log agent-recommended jobs as link-only Tracker stubs (pending).

    Each item is {"url", "title"?, "company"?, "location"?, "recommended_by"?,
    "reason"?, "country"?} or a bare URL string, saved separately so it keeps its own
    identity fields. A per-item recommended_by overrides the batch default;
    reason (why the job fits) is stored as matched_jobs.recommend_reason.
    country (name, ISO code, or "Remote") is stored as matched_jobs.country
    and wins over the country derived from location.

    A job_id already known anywhere (matched_jobs, jobs, or timeout_jobs;
    Indeed: all jk variants) is skipped with no write at all — unlike Log
    Job, an agent push must not retag, rename, or wipe a scraped JD.

    Unmatched / awaiting_match rows are stamped with agent_recommended_by
    so Overview and Unmatched can show the disagreement. Tracker / timeout
    skips are reported only.

    Returns {"ok", "skipped", "failed"} URL lists, "existing" detail rows,
    and skip-bucket counts (already_on_tracker, below_threshold,
    awaiting_match, timeout).
    """
    merged = _empty_result()
    for item in jobs or []:
        if isinstance(item, str):
            item = {"url": item}
        if not isinstance(item, dict):
            continue
        raw = str(item.get("url") or "").strip()
        url = expand_short_job_url(raw) or raw
        if not url:
            continue
        source = detect_source(url)
        job_id = resolve_job_id(url, source)
        if not job_id:
            print(f"  ❌ Cannot resolve job_id from URL: {url}")
            merged["failed"].append(url)
            continue
        rec = normalize_recommended_by(item.get("recommended_by")) or recommended_by
        hit = lookup_known_job(job_id, source, link=url)
        if hit:
            print(
                f"  ⏭️  Already in DB ({hit['collection']}, stage={hit['stage']}): "
                f"job_id={job_id} source={source} — not written"
            )
            if hit.get("stage") in ("unmatched", "awaiting_match"):
                stamp_agent_recommendation(hit, rec, item.get("reason"))
            merged["skipped"].append(url)
            merged["existing"].append(existing_from_hit(url, job_id, hit))
            continue
        identity = normalize_manual_identity(
            item.get("title"), item.get("company"), item.get("location")
        )
        result = save_link_only_urls(
            [url],
            status="pending",
            recommended_by=rec,
            identity=identity,
            recommend_reason=item.get("reason"),
            country=item.get("country"),
        ) or {}
        if url in result.get("ok", []):
            merged["ok"].append(url)
        elif url in result.get("skipped", []):
            merged["skipped"].append(url)
        else:
            merged["failed"].append(url)
    merged.update(_count_skip_buckets(merged["existing"]))
    return merged
