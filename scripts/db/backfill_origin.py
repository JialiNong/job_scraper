#!/usr/bin/env python3
"""
Stamp `origin` (pipeline / manual / agent / api) on Tracker rows saved before
the field existed, and move API-source rows stamped "pipeline" to "api".
Other rows that already have origin are left alone.

Rules (core.job_origin.infer_origin):
  from_unmatched / from_timeout     -> pipeline
  has created_at + recommended_by   -> agent    (only Log Job sets created_at)
  has created_at                    -> manual
  otherwise                         -> pipeline
  API source (ats, arbeitsagentur, …) -> api instead of pipeline

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
from core.job_origin import API_SOURCES, JOB_ORIGINS, ORIGIN_PIPELINE, infer_origin

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")
    args = parser.parse_args()

    col = get_collection("matched_jobs")
    query = {"$or": [
        {"origin": {"$nin": list(JOB_ORIGINS)}},
        {"origin": ORIGIN_PIPELINE, "source": {"$in": sorted(API_SOURCES)}},
    ]}
    fields = {"created_at": 1, "recommended_by": 1, "from_unmatched": 1, "from_timeout": 1,
              "origin": 1, "source": 1}

    counts = Counter()
    ops = []
    for doc in col.find(query, fields):
        origin = infer_origin(doc)
        counts[origin] += 1
        ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": {"origin": origin}}))

    print(f"Rows to stamp: {len(ops)}")
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
