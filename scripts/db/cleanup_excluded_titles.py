#!/usr/bin/env python3
"""
Delete jobs that fail pre-filter rules used by the scrapers:

1. Title matches TITLE_EXCLUDE_KEYWORDS
2. Description is not English (DE / FR / …; same english-only gate as scrapers).
   Still reported under the german_filtered bucket in the UI.

Jobs already present in matched_jobs are never deleted.
Optionally reset AI analysis fields on remaining non-matched jobs
so ai_matcher can re-process them.
"""
import argparse
import os
import sys

# Allow imports from src/ when run from project root
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src")
sys.path.insert(0, _SRC)

from dotenv import load_dotenv

from core.db_mongo import get_collection
from core.scraper_utils import (
    is_non_english_job_detail,
    is_title_excluded,
)

load_dotenv()

# Fields written by mark_job_as_matched / AI analysis
_AI_FIELDS = [
    "matched_at",
    "match_score",
    "recommendation",
    "special_match",
    "special_match_reasons",
    "disqualification_reason",
    "match_reasons",
    "missing_requirements",
    "red_flags",
    "nice_to_have_matches",
    "summary",
    "what_youll_do",
    "what_theyre_looking_for",
]

_SAMPLE_LIMIT = 15


def _protected_keys(matched_coll):
    """Return set of (job_id, source) already in matched_jobs."""
    return {
        (doc.get("job_id"), doc.get("source"))
        for doc in matched_coll.find({}, {"job_id": 1, "source": 1})
    }


def _exclusion_reasons(job):
    """
    Return list of reason tags if this job fails pre-filters.
    Empty list means keep.
    """
    reasons = []
    title = job.get("title") or ""
    if is_title_excluded(title):
        reasons.append("title")

    description = job.get("description") or ""
    should_skip, lang, german_share = is_non_english_job_detail(description)
    if should_skip:
        reasons.append(f"non_en:{lang}:{german_share:.0%}")

    return reasons


def find_excluded_jobs(jobs_coll, protected):
    """
    Find jobs failing title and/or non-English description filters,
    excluding those already in matched_jobs.
    """
    to_delete = []  # list of (job, reasons)
    protected_hits = []  # list of (job, reasons)
    title_count = 0
    german_count = 0

    cursor = jobs_coll.find(
        {},
        {
            "_id": 1,
            "title": 1,
            "job_id": 1,
            "source": 1,
            "company": 1,
            "description": 1,
        },
    )
    total = jobs_coll.count_documents({})
    for i, job in enumerate(cursor, 1):
        if i % 50 == 0 or i == total:
            print(f"  scanning {i}/{total}...", flush=True)

        reasons = _exclusion_reasons(job)
        if not reasons:
            continue

        if any(r == "title" for r in reasons):
            title_count += 1
        if any(r.startswith("non_en:") for r in reasons):
            german_count += 1

        key = (job.get("job_id"), job.get("source"))
        if key in protected:
            protected_hits.append((job, reasons))
            continue

        to_delete.append((job, reasons))

    return to_delete, protected_hits, title_count, german_count


def delete_jobs(jobs_coll, to_delete, dry_run):
    if not to_delete:
        print("Nothing to delete.")
        return 0

    if dry_run:
        print(f"[dry-run] Would delete {len(to_delete)} jobs. Re-run with --execute to apply.")
        return 0

    ids = [job["_id"] for job, _ in to_delete]
    result = jobs_coll.delete_many({"_id": {"$in": ids}})
    print(f"Deleted {result.deleted_count} jobs.")
    return result.deleted_count


def reset_ai_on_remaining(jobs_coll, protected, dry_run):
    """
    Clear AI analysis on jobs that are NOT in matched_jobs,
    so get_new_jobs() will pick them up again.
    """
    candidates = list(
        jobs_coll.find(
            {"matched_at": {"$exists": True}},
            {"_id": 1, "job_id": 1, "source": 1, "title": 1},
        )
    )

    to_reset = [
        job
        for job in candidates
        if (job.get("job_id"), job.get("source")) not in protected
    ]

    print(f"\nAI reset candidates (analyzed, not in matched_jobs): {len(to_reset)}")
    for job in to_reset[:_SAMPLE_LIMIT]:
        print(f"  - [{job.get('source')}] {job.get('title')}")
    if len(to_reset) > _SAMPLE_LIMIT:
        print(f"  ... and {len(to_reset) - _SAMPLE_LIMIT} more")

    if not to_reset:
        return 0

    if dry_run:
        print(f"[dry-run] Would reset AI fields on {len(to_reset)} jobs. Re-run with --execute.")
        return 0

    ids = [job["_id"] for job in to_reset]
    result = jobs_coll.update_many(
        {"_id": {"$in": ids}},
        {
            "$unset": {field: "" for field in _AI_FIELDS},
            "$set": {"status": "new"},
        },
    )
    print(f"Reset AI fields on {result.modified_count} jobs.")
    return result.modified_count


def _print_samples(label, items):
    print(f"\n{label}:")
    for job, reasons in items[:_SAMPLE_LIMIT]:
        reason_str = ", ".join(reasons)
        print(
            f"  - [{job.get('source')}] {job.get('title')} "
            f"@ {job.get('company') or '-'} ({reason_str})"
        )
    if len(items) > _SAMPLE_LIMIT:
        print(f"  ... and {len(items) - _SAMPLE_LIMIT} more")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Delete jobs failing title exclude keywords or the English-only "
            "description gate (non-EN still counted as german_filtered; "
            "keeps anything already in matched_jobs)."
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete/reset. Without this flag, only dry-run.",
    )
    parser.add_argument(
        "--reset-ai",
        action="store_true",
        help=(
            "Also clear AI analysis on remaining jobs not in matched_jobs, "
            "so ai_matcher can re-process them."
        ),
    )
    args = parser.parse_args()
    dry_run = not args.execute

    jobs = get_collection("jobs")
    matched = get_collection("matched_jobs")

    total_jobs = jobs.count_documents({})
    protected = _protected_keys(matched)
    print(f"jobs total: {total_jobs}")
    print(f"matched_jobs protected keys: {len(protected)}")
    print("Language gate: English only (non-EN → german_filtered)")
    print(f"mode: {'DRY-RUN' if dry_run else 'EXECUTE'}")
    print("\nScanning jobs (title + description language)...")

    to_delete, protected_hits, title_count, german_count = find_excluded_jobs(
        jobs, protected
    )

    print(f"\nFail title filter (any, incl. protected): {title_count}")
    print(f"Fail non-English filter (any, incl. protected): {german_count}")
    print(f"Would delete: {len(to_delete)}")
    print(f"Protected (keep, in matched_jobs): {len(protected_hits)}")

    _print_samples("Sample deletions", to_delete)
    if protected_hits:
        _print_samples("Sample protected (kept because in matched_jobs)", protected_hits)

    delete_jobs(jobs, to_delete, dry_run=dry_run)

    if args.reset_ai:
        protected = _protected_keys(matched)
        reset_ai_on_remaining(jobs, protected, dry_run=dry_run)

    if dry_run:
        print("\nNext steps:")
        print("  1. Review the list above")
        print("  2. python3 scripts/db/cleanup_excluded_titles.py --execute")
        print("  3. python3 scripts/db/cleanup_excluded_titles.py --execute --reset-ai")
        print("  4. python3 src/matching/ai_matcher.py -l 20   # smoke-test then full run")


if __name__ == "__main__":
    main()
