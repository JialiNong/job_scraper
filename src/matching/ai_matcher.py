#!/usr/bin/env python3
"""
AI job matching — pipeline entry point.

For every scraped job without matched_at: run matching.evaluator (model labels the
job and company, code scores it), write the result onto the jobs document, and copy
jobs at or above MATCH_THRESHOLD to matched_jobs (the Tracker).

Used by scripts/lib/pipeline_common.sh (`ai_matcher.py --source indeed|linkedin`),
manual_apply_scraper (Log Job / Run AI) via evaluate_job().
"""
import os
import sys
from datetime import datetime
from typing import Dict, Optional

# Add src/ to path so package imports work when run as a script
_SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

from dotenv import load_dotenv

load_dotenv()

from core.db_mongo import (  # noqa: E402
    get_collection,
    get_new_jobs,
    init_db,
    mark_job_as_matched,
)
from core.job_origin import found_origin  # noqa: E402
from core.manual_log import analysis_fields  # noqa: E402
from core.job_url import posting_key_fields  # noqa: E402
from core.scraper_utils import indeed_job_id_variants, scrub_bullet_location  # noqa: E402
from matching import evaluator  # noqa: E402
from matching.evaluator import (  # noqa: E402,F401  (re-exported for callers)
    is_blocked_match_company,
    load_matching_rules,
    load_user_profile,
)

MATCH_THRESHOLD = float(os.getenv("MATCH_THRESHOLD", "7.0"))  # Tracker cut-off (0-10)
# Set by scripts/run_jobs.sh so the end-of-run Telegram digest only lists this run's matches
PIPELINE_RUN_ID = os.getenv("PIPELINE_RUN_ID", "").strip()


def evaluate_job(
    job: Dict,
    user_profile: Optional[str] = None,
    rules: Optional[str] = None,
    company_cache=None,
) -> Optional[Dict]:
    """
    Evaluate one job and return the stored analysis dict, or None if the model
    call failed. Company lookups are cached in MongoDB unless a cache is given.
    """
    if company_cache is None:
        try:
            company_cache = evaluator.MongoCompanyCache()
        except Exception as exc:
            print(f"⚠ Company cache unavailable ({exc}); looking up without cache")
            company_cache = {}
    result = evaluator.evaluate(
        job, cv=user_profile, rules=rules, company_cache=company_cache
    )
    return evaluator.to_analysis(result, threshold=MATCH_THRESHOLD)


def save_matched_job(job: Dict, analysis: Dict) -> bool:
    """Copy a job at/above the threshold into matched_jobs (skip if it is already there)."""
    try:
        company = job.get("company", "")
        if is_blocked_match_company(company):
            print(f"✗ Blocked company ({company!r}) — not written to matched_jobs")
            return False

        matched_jobs = get_collection("matched_jobs")
        existing_query = {"job_id": job["job_id"], "source": job["source"]}
        if job.get("source") == "indeed":
            variants = indeed_job_id_variants(job["job_id"])
            if variants:
                existing_query = {"job_id": {"$in": variants}, "source": "indeed"}
        if matched_jobs.find_one(existing_query):
            print(f"Matched job {job['job_id']} already exists, skip")
            return False

        matched_jobs.insert_one({
            "title": job.get("title", ""),
            "company": company,
            "location": scrub_bullet_location(job.get("location", "")),
            "link": job.get("link", ""),
            "job_id": job.get("job_id", ""),
            "source": job.get("source", ""),
            **posting_key_fields(job.get("link", "")),
            "description": job.get("description", ""),
            "applicants": job.get("applicants", ""),
            "match_score": analysis.get("match_score", 0),
            **analysis_fields(analysis),
            "status": "pending",
            # Local wall clock — same basis as Telegram / Web "today" filters
            "matched_at": datetime.now(),
            "applied_at": None,
            "application_timeline": [],
            "notes": "",
            "application_qa": [],
            "highlights": [],
            "origin": found_origin(job.get("source")),
            **({"run_id": PIPELINE_RUN_ID} if PIPELINE_RUN_ID else {}),
        })
        print(f"✓ Saved matched job: {job.get('title')} (score: {analysis.get('match_score')})")
        return True
    except Exception as e:
        print(f"Error saving matched job: {e}")
        return False


def process_new_jobs(limit: Optional[int] = None, source: Optional[str] = None):
    """Evaluate every job without matched_at (optionally one source / a limit)."""
    print("=== Starting AI Job Matching ===")
    print(f"Job model: {evaluator.JOB_MODEL} · company model: {evaluator.COMPANY_MODEL}")

    user_profile = load_user_profile()
    if not user_profile:
        print("Error: No user profile found. Create docs/user_profile.md first.")
        return
    rules = load_matching_rules()
    if not rules:
        print("Error: docs/matching_rules.md is missing.")
        return

    new_jobs = get_new_jobs(limit=limit, source=source)
    total = len(new_jobs)
    print(f"Found {total} new jobs to analyze")
    if not total:
        return

    try:
        cache = evaluator.MongoCompanyCache()
    except Exception as exc:
        print(f"⚠ Company cache unavailable ({exc}); looking up without cache")
        cache = {}

    matched = 0
    gates = {}
    for i, job in enumerate(new_jobs, 1):
        print(f"\n--- Job {i}/{total}: {job.get('title')} | {job.get('company')} ({job.get('source')})")
        try:
            analysis = evaluate_job(job, user_profile, rules, company_cache=cache)
        except evaluator.OutOfCreditsError as exc:
            print(f"✗ {exc} — stopping; remaining jobs stay unmatched for the next run")
            break

        if not analysis:
            print("⚠ AI analysis failed — marked so it is not retried forever")
            mark_job_as_matched(
                job.get("job_id"),
                job.get("source"),
                match_score=0,
                analysis={
                    "recommendation": "No",
                    "disqualification_reason": "AI analysis failed",
                    "summary": "AI analysis failed; skipped to avoid reprocessing.",
                    "match_gate": "score_failed",
                },
            )
            continue

        score = analysis["match_score"]
        gate = analysis["match_gate"]
        gates[gate] = gates.get(gate, 0) + 1
        print(f"Score: {score}/10 · gate: {gate}")
        if analysis.get("disqualification_reason"):
            print(f"  ⚠ {analysis['disqualification_reason'][:200]}")
        for reason in analysis.get("match_reasons", []):
            print(f"  • {reason[:200]}")

        if score >= MATCH_THRESHOLD:
            if save_matched_job(job, analysis):
                matched += 1
        else:
            print(f"✗ Below threshold ({MATCH_THRESHOLD}); kept on Unmatched")

        mark_job_as_matched(
            job.get("job_id"),
            job.get("source"),
            match_score=score,
            analysis=analysis_fields(analysis),
        )

    print("\n=== Matching Complete ===")
    print("Gates: " + ", ".join(f"{k} {v}" for k, v in sorted(gates.items())))
    print(f"Matched (≥ {MATCH_THRESHOLD}): {matched}")


def main():
    """CLI: python3 src/matching/ai_matcher.py [-l N] [-s indeed|linkedin] [-t 7.0]"""
    global MATCH_THRESHOLD
    import argparse

    parser = argparse.ArgumentParser(description="AI-powered job matching")
    parser.add_argument("--limit", "-l", type=int, default=None,
                        help="Limit number of jobs to process")
    parser.add_argument("--source", "-s",
                        choices=["indeed", "linkedin", "arbeitnow", "arbeitsagentur", "adzuna", "ats"],
                        default=None,
                        help="Filter by job source")
    parser.add_argument("--threshold", "-t", type=float, default=MATCH_THRESHOLD,
                        help="Minimum score for the Tracker (0-10)")
    args = parser.parse_args()
    MATCH_THRESHOLD = args.threshold

    init_db()
    process_new_jobs(limit=args.limit, source=args.source)


if __name__ == "__main__":
    main()
