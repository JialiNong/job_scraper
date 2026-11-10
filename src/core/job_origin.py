"""
How a Tracker row (matched_jobs) first got there — stored as `origin`.

    pipeline  the browser scrapers (LinkedIn / Indeed) found it (matched by
              ai_matcher, or added later from the Unmatched / Timeouts pages)
    api       an API source in src/sources/ found it (ATS, Bundesagentur,
              Arbeitnow, Adzuna) — the source badge says which one
    manual    Log Job with no agent tag (pasted link)
    agent     Log Job / agent push with recommended_by (which agent is in
              recommended_by)

`origin` is written once on insert and never overwritten, so marking a
pipeline job applied via Log Job keeps it "pipeline".
"""
from __future__ import annotations

ORIGIN_PIPELINE = "pipeline"
ORIGIN_MANUAL = "manual"
ORIGIN_AGENT = "agent"
ORIGIN_API = "api"
JOB_ORIGINS = (ORIGIN_PIPELINE, ORIGIN_MANUAL, ORIGIN_AGENT, ORIGIN_API)

# `source` values written by src/sources/ (not the browser scrapers)
API_SOURCES = frozenset({"ats", "arbeitsagentur", "arbeitnow", "adzuna"})

_AGENTS = ("claude", "cursor", "gemini", "gpt")


def normalize_origin(value) -> str:
    key = str(value or "").strip().lower()
    return key if key in JOB_ORIGINS else ""


def is_api_source(source) -> bool:
    return str(source or "").strip().lower() in API_SOURCES


def found_origin(source) -> str:
    """Origin for a job the automated pipeline found: api for src/sources/, else pipeline."""
    return ORIGIN_API if is_api_source(source) else ORIGIN_PIPELINE


def log_job_origin(recommended_by) -> str:
    """Origin for a row first inserted by Log Job."""
    rec = str(recommended_by or "").strip().lower()
    return ORIGIN_AGENT if rec in _AGENTS else ORIGIN_MANUAL


def infer_origin(job: dict | None) -> str:
    """
    Best guess for rows saved before `origin` existed.

    Only Log Job (force_save_manual_job) sets matched_jobs.created_at on
    insert, except Unmatched "Can Apply" copies (from_unmatched). Pipeline
    inserts never set it.
    """
    job = job or {}
    stored = normalize_origin(job.get("origin"))
    if stored == ORIGIN_PIPELINE:
        # API rows matched before ORIGIN_API existed were stamped "pipeline"
        return found_origin(job.get("source"))
    if stored:
        return stored
    if job.get("from_unmatched") or job.get("from_timeout"):
        return found_origin(job.get("source"))
    if job.get("created_at"):
        return log_job_origin(job.get("recommended_by"))
    return found_origin(job.get("source"))


def origin_filter_query(origin: str) -> dict:
    """Tracker filter on the stored field (run scripts/db/backfill_origin.py once for old rows)."""
    origin = normalize_origin(origin)
    return {"origin": origin} if origin else {}
