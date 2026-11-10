#!/usr/bin/env python3
"""
Fill `posted_at` (the employer's publish date) on rows saved before it was
copied to the Tracker or parsed for every ATS.

  1. jobs (source=ats) without posted_at: re-read the watched ATS boards and
     take the date of the same job_id (Recruitee dates were not parsed before).
  2. matched_jobs without posted_at: copy it from the jobs row with the same
     job_id + source.

Dry run by default; pass --apply to write.
"""
import argparse
import os
import sys
from collections import Counter

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src")
sys.path.insert(0, _SRC)

from dotenv import load_dotenv
from pymongo import UpdateOne

load_dotenv()

from core.db_mongo import get_db  # noqa: E402
from sources import ats  # noqa: E402

MISSING = {"$in": [None, ""]}


def fill_ats_jobs(db, apply: bool) -> Counter:
    stats = Counter()
    missing = {
        r["job_id"]: r["_id"]
        for r in db.jobs.find({"source": ats.SOURCE, "posted_at": MISSING}, {"job_id": 1})
    }
    stats["missing"] = len(missing)
    if not missing:
        return stats
    ops = []
    for job in ats.fetch(log=lambda msg: print(f"  {msg}")):
        oid = missing.get(job.job_id)
        if oid is None:
            continue
        if job.posted_at:
            ops.append(UpdateOne({"_id": oid}, {"$set": {"posted_at": job.posted_at}}))
        else:
            stats["board_has_no_date"] += 1
    stats["filled"] = len(ops)
    stats["closed_or_unwatched"] = stats["missing"] - len(ops) - stats["board_has_no_date"]
    if apply and ops:
        db.jobs.bulk_write(ops, ordered=False)
    return stats


def copy_to_tracker(db, apply: bool) -> Counter:
    stats = Counter()
    ops = []
    for row in db.matched_jobs.find({"posted_at": MISSING}, {"job_id": 1, "source": 1}):
        if not row.get("job_id"):
            continue
        src = db.jobs.find_one(
            {"job_id": row["job_id"], "source": row.get("source"), "posted_at": {"$type": "date"}},
            {"posted_at": 1},
        )
        if not src:
            continue
        ops.append(UpdateOne({"_id": row["_id"]}, {"$set": {"posted_at": src["posted_at"]}}))
        stats[row.get("source") or "?"] += 1
    if apply and ops:
        db.matched_jobs.bulk_write(ops, ordered=False)
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="write posted_at (default: dry run)")
    args = parser.parse_args()
    db = get_db()
    suffix = "" if args.apply else "  (dry run)"

    print(f"jobs (ATS, re-read boards): {dict(fill_ats_jobs(db, args.apply))}{suffix}")
    # In a dry run step 1 wrote nothing, so this counts only rows already dated
    print(f"matched_jobs (copied from jobs): {dict(copy_to_tracker(db, args.apply))}{suffix}")


if __name__ == "__main__":
    main()
