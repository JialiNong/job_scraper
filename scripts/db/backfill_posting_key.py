#!/usr/bin/env python3
"""
Stamp `posting_key` (core.job_url.posting_key) on jobs / matched_jobs /
timeout_jobs rows saved before the field existed, then list ads that are
stored more than once under different ids (e.g. a Personio link logged by
hand and the same posting found by the ATS source with ?language=en).

The duplicate report is read-only: pick which card to keep in the Tracker.

Dry run by default; pass --apply to write.
"""
import argparse
import os
import sys
from collections import Counter, defaultdict

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src")
sys.path.insert(0, _SRC)

from dotenv import load_dotenv
from pymongo import UpdateOne

load_dotenv()

from core.db_mongo import ensure_indexes, get_db  # noqa: E402
from core.job_url import posting_key  # noqa: E402

COLLECTIONS = ("jobs", "matched_jobs", "timeout_jobs")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="write posting_key (default: dry run)")
    args = parser.parse_args()

    db = get_db()
    if args.apply:
        ensure_indexes()

    for name in COLLECTIONS:
        col = db[name]
        stats = Counter()
        ops = []
        groups = defaultdict(list)
        for row in col.find({}, {"link": 1, "posting_key": 1, "job_id": 1, "source": 1, "title": 1, "status": 1}):
            key = posting_key(row.get("link") or "")
            if not key:
                stats["no_key"] += 1
                continue
            groups[key].append(row)
            if row.get("posting_key") == key:
                stats["already"] += 1
                continue
            stats["stamp"] += 1
            ops.append(UpdateOne({"_id": row["_id"]}, {"$set": {"posting_key": key}}))
        if args.apply and ops:
            col.bulk_write(ops, ordered=False)
        print(f"{name}: {dict(stats)}" + ("" if args.apply else "  (dry run)"))

        dups = {k: rows for k, rows in groups.items()
                if len({(r.get("source"), r.get("job_id")) for r in rows}) > 1}
        for key, rows in sorted(dups.items()):
            print(f"  duplicate {key}")
            for r in rows:
                print(f"    - {r.get('source')}/{r.get('job_id')} [{r.get('status')}] {r.get('title')}")


if __name__ == "__main__":
    main()
