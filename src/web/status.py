"""
Tracker status + application timeline rules.

Pure functions (no Flask, no DB): status labels, timeline parsing and
hydration, progress chips, and the fields PATCH /status and /timeline write.
"""
import os
from datetime import datetime
from typing import Optional

from core.db_mongo import APPLIED_STATUSES


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
    "closed_no_reply": "Closed · No Reply",
    "prev_rejected": "Prev. Rejected",
    "too_many_questions": "Skipped · Long Form",
    "repost":     "Repost",
    "duplicate":  "Duplicate",
}

# Other-dropdown overlay statuses. They do not wipe an existing application
# record (applied_at / timeline); only Reset Not Applied (pending) does.
# "closed": closed before I applied; "closed_no_reply": closed after I applied
# with no response; "prev_rejected": same role (new job id) already rejected
# me earlier, so skip it this time; "too_many_questions": JD fits but the
# application has too many required questions, so skipped.
SIDE_STATUSES = (
    "unsuitable", "closed", "closed_no_reply", "prev_rejected",
    "too_many_questions", "repost", "duplicate",
)

# Default Tracker view: Not Applied plus application progress. Side statuses
# stay hidden until their own chip (or All) is picked.
ACTIVE_STATUS_FILTER = "active"

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


# English label → DB value (reverse mapping used nowhere yet, kept for clarity)
STATUS_REVERSE = {v: k for k, v in STATUS_MAP.items()}

MATCH_THRESHOLD = float(os.getenv("MATCH_THRESHOLD", "7.0"))


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
    if status_filter == ACTIVE_STATUS_FILTER:
        return (job.get("status") or "pending") not in SIDE_STATUSES
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
    if status_filter == ACTIVE_STATUS_FILTER:
        return {"status": {"$nin": list(SIDE_STATUSES)}}
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


def chip_status_counts(jobs: list) -> dict:
    """
    Filter-chip counts.

    Overlay chips (pending / side statuses) use the status field.
    Progress chips use application_progress, so a side-status interview
    increments both Unsuitable and Interview.
    """
    counts: dict = {ACTIVE_STATUS_FILTER: 0}
    for job in jobs:
        status = job.get("status") or "pending"
        if status not in SIDE_STATUSES:
            counts[ACTIVE_STATUS_FILTER] += 1
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
        update_fields["applicant_position"] = None
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
        update_fields["applicant_position"] = None
    elif existing_applied:
        # Do not restamp a side-status job; allow an Applied-step edit otherwise
        if applied_event and prev_status not in SIDE_STATUSES:
            update_fields["applied_at"] = applied_event["at"]
    elif applied_event:
        update_fields["applied_at"] = applied_event["at"]
    elif timeline:
        update_fields["applied_at"] = timeline[0]["at"]

    return update_fields
