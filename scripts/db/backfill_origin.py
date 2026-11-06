#!/usr/bin/env python3
"""
Stamp `origin` (pipeline / manual / agent) on Tracker rows saved before the
field existed. Rows that already have origin are left alone.

Rules (core.job_origin.infer_origin):
  from_unmatched / from_timeout     -> pipeline
  has created_at + recommended_by   -> agent    (only Log Job sets created_at)
  has created_at                    -> manual
  otherwise                         -> pipeline

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

from core.db_mongo import get_collection
from core.job_origin import JOB_ORIGINS, infer_origin

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")
    args = parser.parse_args()

    col = get_collection("matched_jobs")
    query = {"origin": {"$nin": list(JOB_ORIGINS)}}
    fields = {"created_at": 1, "recommended_by": 1, "from_unmatched": 1, "from_timeout": 1, "origin": 1}

    counts = Counter()
    ops = []
    for doc in col.find(query, fields):
        origin = infer_origin(doc)
        counts[origin] += 1
        ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": {"origin": origin}}))

    print(f"Rows without origin: {len(ops)}")
    for origin in JOB_ORIGINS:
        print(f"  {origin:9s} {counts[origin]}")

    if not args.apply:
        print("Dry run — pass --apply to write.")
        return
    if ops:
        result = col.bulk_write(ops, ordered=False)
        print(f"Updated {result.modified_count} rows.")
    col.create_index("origin")


if __name__ == "__main__":
    main()
