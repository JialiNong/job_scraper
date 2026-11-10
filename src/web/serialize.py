"""Turn Mongo job documents into the JSON the Tracker UI expects."""
from datetime import datetime

from core.country import COUNTRY_LABELS, job_country
from core.db_mongo import get_collection
from core.job_origin import infer_origin
from core.manual_log import (
    IDENTITY_FIELDS,
    can_fetch_details,
    job_missing_details,
    normalize_recommended_by,
)
from web.list_query import _company_key, company_match_clause
from web.status import (
    TIMELINE_STEPS,
    _format_timeline_at,
    _hydrate_timeline,
    application_progress,
)


COMPANY_HISTORY_LIMIT = 30


def company_history_item(job: dict) -> dict:
    """Short summary of one Tracker job, shown under 'Seen this company before'."""
    def _fmt(value):
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d")
        return str(value or "")[:10]

    timeline = _hydrate_timeline(job)
    return {
        "_id": str(job["_id"]),
        "title": job.get("title") or "",
        "status": job.get("status") or "pending",
        "progress": application_progress(job) or "",
        "applied_at": _fmt(job.get("applied_at")),
        "matched_at": _fmt(job.get("matched_at")),
        "last_step_at": _fmt(timeline[-1].get("at")) if timeline else "",
        "source": job.get("source") or "",
        "applicant_position": job.get("applicant_position"),
    }


def attach_company_history(jobs: list, collection=None) -> list:
    """
    Add `company_history` (other Tracker jobs at the same company, newest
    first) to each serialized job. One query for the whole page.
    """
    names = {}
    for job in jobs:
        key = _company_key(job.get("company"))
        if key:
            names.setdefault(key, job.get("company"))
    if not names:
        for job in jobs:
            job["company_history"] = []
        return jobs

    collection = collection if collection is not None else get_collection("matched_jobs")
    clauses = [company_match_clause(name) for name in names.values()]
    projection = {
        "title": 1, "company": 1, "status": 1, "source": 1,
        "applied_at": 1, "matched_at": 1, "updated_at": 1,
        "application_timeline": 1, "applicant_position": 1,
    }
    by_company: dict = {}
    for doc in collection.find({"$or": clauses}, projection).sort("matched_at", -1):
        by_company.setdefault(_company_key(doc.get("company")), []).append(doc)

    for job in jobs:
        key = _company_key(job.get("company"))
        others = [
            d for d in by_company.get(key, [])
            if str(d["_id"]) != str(job.get("_id"))
        ]
        job["company_history"] = [company_history_item(d) for d in others[:COMPANY_HISTORY_LIMIT]]
        job["company_history_total"] = len(others)
    return jobs


def parse_applicant_position(raw) -> tuple:
    """
    Validate the 'I was applicant #N' value. Returns (value, error).
    Empty / None clears the field.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, None
    if isinstance(raw, bool):
        return None, "applicant_position must be a whole number"
    try:
        value = int(str(raw).strip().lstrip("#"))
    except (TypeError, ValueError):
        return None, "applicant_position must be a whole number"
    if value < 1 or value > 100000:
        return None, "applicant_position must be between 1 and 100000"
    return value, None


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
    # Employer's publish date (API sources): date only, UTC
    if isinstance(job.get("posted_at"), datetime):
        job["posted_at"] = job["posted_at"].strftime("%Y-%m-%d")
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
    # "I was applicant #N" at apply time (LinkedIn); None for older records
    pos, _err = parse_applicant_position(job.get("applicant_position"))
    job["applicant_position"] = pos
    rec = normalize_recommended_by(job.get("recommended_by"))
    job["recommended_by"] = rec
    job["recommend_reason"] = str(job.get("recommend_reason") or "")
    job["origin"] = infer_origin(job)
    job["details_missing"] = job_missing_details(job)
    job["country"] = job_country(job.get("location"), job.get("country"))
    job["country_label"] = COUNTRY_LABELS.get(job["country"], "")
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
