#!/usr/bin/env python3
"""
Batch-log agent-recommended jobs to the Tracker as link-only stubs.

Local CLI twin of POST /api/agent/jobs (which the Claude daily job push
calls on Render). Each job lands on the Tracker as Not Applied (pending)
with recommended_by set. No browser, no AI — use Tracker
"Select missing details" → Run AI for selected to score them later.

Input (file path or "-" for stdin):

    [
      {"url": "https://www.linkedin.com/jobs/view/4123456789/",
       "title": "Backend Engineer", "company": "Acme", "location": "Berlin",
       "reason": "Python + FastAPI backend, English-only team",
       "country": "Germany"},   # optional: name, ISO code, or "Remote"
      ...
    ]

A job_id already in matched_jobs / jobs / timeout_jobs is skipped with no write.
Skips on Unmatched are reported as below_threshold (not on the Tracker).
"""
import argparse
import json
import os
import sys

# Allow imports from src/ when run from project root
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src")
sys.path.insert(0, _SRC)

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(_SRC), ".env"))

from core.db_mongo import init_db  # noqa: E402
from core.agent_push import log_agent_jobs  # noqa: E402
from core.manual_log import RECOMMENDED_BY_AGENTS  # noqa: E402


def load_jobs(path: str) -> list:
    """Read the JSON job list; accepts a bare list or {"jobs": [...]}."""
    if path == "-":
        data = json.load(sys.stdin)
    else:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    if isinstance(data, dict):
        data = data.get("jobs") or []
    if not isinstance(data, list):
        raise ValueError("Expected a JSON list of jobs")
    return data


def main():
    parser = argparse.ArgumentParser(
        description="Log agent-recommended jobs to the Tracker (link-only, Not Applied)."
    )
    parser.add_argument("file", help='JSON file with the job list, or "-" for stdin')
    parser.add_argument(
        "--recommended-by",
        choices=RECOMMENDED_BY_AGENTS,
        default="claude",
        help="Agent tag stored on the Tracker card (default: claude)",
    )
    args = parser.parse_args()

    try:
        jobs = load_jobs(args.file)
    except (OSError, ValueError) as exc:
        print(f"❌ Cannot read jobs: {exc}")
        sys.exit(1)
    if not jobs:
        print("No jobs to log.")
        return

    init_db()
    print(f"📂 Logging {len(jobs)} job(s) recommended by {args.recommended_by}")
    result = log_agent_jobs(jobs, recommended_by=args.recommended_by)
    below = int(result.get("below_threshold") or 0)
    print(
        f"\nTotal — ok: {len(result['ok'])}, skipped: {len(result['skipped'])} "
        f"(Tracker {result.get('already_on_tracker', 0)}, "
        f"Unmatched {below}, awaiting {result.get('awaiting_match', 0)}, "
        f"timeout {result.get('timeout', 0)}), "
        f"failed: {len(result['failed'])}"
    )
    if below:
        print("Unmatched (agent recommended, not on Tracker):")
        for item in result.get("existing") or []:
            if item.get("stage") != "unmatched":
                continue
            score = item.get("match_score")
            score_s = f" score {score}" if score is not None else ""
            title = item.get("title") or item.get("url")
            company = item.get("company") or ""
            who = f"{title} @ {company}".strip(" @")
            print(f"  • {who}{score_s}  {item.get('open_path') or ''}")


if __name__ == "__main__":
    main()
