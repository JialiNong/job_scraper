#!/usr/bin/env python3
"""
Check the matcher (src/matching/evaluator.py) against Jiali's own Tracker decisions.
"old" is the score stored on each card by the matcher that produced it.

Labels come from matched_jobs.status:
  positive = she applied (applied / rejected / interview / prev_rejected / closed_no_reply)
  negative = she marked it unsuitable
A third set re-checks jobs the current German rule gate rejected.

Read-only on MongoDB. Results go to evals/results/ (gitignored); company lookups are
cached in evals/results/company_cache.json so reruns do not search again.

Usage:
  python3 scripts/tools/eval_matcher.py                    # 40 pos, 40 neg, 25 German
  python3 scripts/tools/eval_matcher.py --pos 10 --neg 10 --german 5
  python3 scripts/tools/eval_matcher.py --job-model gpt-5.5
  python3 scripts/tools/eval_matcher.py --unapplied        # every Tracker job she did not apply to
  python3 scripts/tools/eval_matcher.py --unapplied --limit 100   # the 100 most recent ones
"""
import argparse
import json
import os
import random
from collections import Counter
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(_ROOT, ".env"))

import matching.evaluator as ev  # noqa: E402
from core.db_mongo import APPLICATION_QUERY, get_collection  # noqa: E402
from matching.evaluator import load_user_profile  # noqa: E402

POSITIVE = ["applied", "rejected", "interview", "prev_rejected", "closed_no_reply"]
RESULTS_DIR = os.path.join(_ROOT, "evals", "results")
CACHE_PATH = os.path.join(RESULTS_DIR, "company_cache.json")
MIN_DESC = 500


def _desc(doc, jobs):
    text = doc.get("description") or ""
    if len(text) < MIN_DESC:
        src = jobs.find_one(
            {"job_id": doc.get("job_id"), "source": doc.get("source")},
            {"description": 1},
        )
        text = (src or {}).get("description") or text
    return text


def build_sample(n_pos, n_neg, n_german, seed):
    rng = random.Random(seed)
    mj = get_collection("matched_jobs")
    jobs = get_collection("jobs")
    fields = {
        "title": 1, "company": 1, "location": 1, "description": 1, "job_id": 1,
        "source": 1, "status": 1, "match_score": 1, "notes": 1, "recommended_by": 1,
        "link": 1,
    }

    def pick(query, n, label):
        docs = list(mj.find(query, fields))
        rng.shuffle(docs)
        out = []
        for d in docs:
            if len(out) >= n:
                break
            d["description"] = _desc(d, jobs)
            if len(d["description"]) < MIN_DESC:
                continue
            d["label"] = label
            out.append(d)
        return out

    sample = pick({"status": {"$in": POSITIVE}}, n_pos, "positive")
    sample += pick({"status": "unsuitable"}, n_neg, "negative")

    german = list(
        jobs.find(
            {"match_gate": "german", "description": {"$exists": True}},
            {**fields, "disqualification_reason": 1},
        )
    )
    rng.shuffle(german)
    for d in german:
        if sum(1 for s in sample if s["label"] == "german_gate") >= n_german:
            break
        if len(d.get("description") or "") < MIN_DESC:
            continue
        d["label"] = "german_gate"
        sample.append(d)
    return sample


def build_unapplied(limit=None):
    """matched_jobs rows with no application (no applied_at / timeline / applied status),
    newest first (by when they were added to the Tracker)."""
    mj = get_collection("matched_jobs")
    jobs = get_collection("jobs")
    out = []
    cursor = mj.find({"$nor": [APPLICATION_QUERY]}).sort("_id", -1)
    if limit:
        cursor = cursor.limit(limit)
    for d in cursor:
        d["description"] = _desc(d, jobs)
        d["label"] = "unapplied"
        out.append(d)
    return out


def report_unapplied(rows):
    def band(score):
        if score is None:
            return "error"
        if score < 6:
            return "<6"
        if score < 7:
            return "6–6.9"
        return "≥7"

    errors = [r for r in rows if r["score"] is None]
    no_jd = [r for r in rows if "description unusable" in r["parts"]]
    judged = [r for r in rows if r not in no_jd and r not in errors]
    print(f"\n=== {len(rows)} Tracker jobs she did not apply to ===")
    print(f"API errors (not scored): {len(errors)}")
    print(f"No usable JD (cannot judge): {len(no_jd)}")
    print(f"Judged: {len(judged)}")
    if not judged:
        return
    for b in ("<6", "6–6.9", "≥7"):
        n = sum(1 for r in judged if band(r["score"]) == b)
        if n:
            print(f"  {b:<6} {n:4}  ({n / len(judged):.0%})")

    print("\nBy her status (judged jobs):")
    statuses = sorted({r["status"] for r in judged}, key=lambda s: -sum(1 for r in judged if r["status"] == s))
    for st in statuses:
        group = [r for r in judged if r["status"] == st]
        cells = "  ".join(f"{b} {sum(1 for r in group if band(r['score']) == b):3}" for b in ("<6", "6–6.9", "≥7"))
        print(f"  {str(st):<20} {len(group):4} | {cells}")

    reasons = Counter()
    for r in judged:
        if (r["score"] or 0) < 6:
            reasons[next((p for p in r["parts"] if p.startswith("hard fail")), "low score (no hard fail)")] += 1
    print("\nWhy below 6:")
    for k, n in reasons.most_common():
        print(f"  {n:4}  {k}")

    print("\nPending (undecided) jobs scoring ≥ 7 — maybe worth a look:")
    for r in sorted(judged, key=lambda r: -(r["score"] or 0)):
        if r["status"] == "pending" and (r["score"] or 0) >= 7:
            print(f"  {r['score']:>4} | {r['title']} | {r['company']} | {r.get('link') or ''}")


def run_one(job, cv, rules, cache):
    if job["label"] == "german_gate":
        labels = ev.classify_job(job, cv=cv, rules=rules)
        return {"labels": labels, "company": None, **ev.score_job(job, labels)}
    return ev.evaluate(job, cv=cv, rules=rules, company_cache=cache)


def _old_score(job):
    try:
        return float(job.get("match_score"))
    except (TypeError, ValueError):
        return None


def report(rows):
    lab = [r for r in rows if r["label"] in ("positive", "negative")]
    pos = [r for r in lab if r["label"] == "positive"]
    neg = [r for r in lab if r["label"] == "negative"]
    print(f"\n=== Tracker decisions: {len(pos)} applied, {len(neg)} unsuitable ===")
    print("How many of each would reach the Tracker, and the unsuitable share among them:")

    def line(name, key, t):
        p = sum(1 for r in pos if (r[key] is not None and r[key] >= t))
        n = sum(1 for r in neg if (r[key] is not None and r[key] >= t))
        share = n / (p + n) if p + n else 0
        print(f"  {name:<10} ≥{t:<4} applied kept {p:3}/{len(pos)}  unsuitable kept {n:3}/{len(neg)}  unsuitable share {share:.0%}")

    for t in (7, 8, 9):
        line("stored", "old_score", t)
    for t in (6, 7, 7.5, 8):
        line("now", "score", t)

    ger = [r for r in rows if r["label"] == "german_gate"]
    if ger:
        agree = sum(1 for r in ger if r["labels"] and r["labels"].get("german_required"))
        print(f"\n=== German rule gate re-check: matcher also says German required for {agree}/{len(ger)} ===")
        for r in ger:
            lb = r["labels"] or {}
            if not lb.get("german_required"):
                print(f"  DISAGREE: {r['title']} | {r['company']}")
                print(f"     rule gate: {(r.get('disqualification_reason') or '')[:160]}")
                print(f"     matcher: {lb.get('summary', '')[:200]}")

    falsely_german = [r for r in pos if r["labels"] and r["labels"].get("german_required")]
    print(f"\nApplied jobs the matcher marks German-required: {len(falsely_german)}")
    for r in falsely_german:
        print(f"  {r['title']} | {r['company']} | {r['labels'].get('german_evidence', '')[:160]}")

    print("\n=== Unsuitable jobs still scored ≥ 7 ===")
    for r in sorted(neg, key=lambda r: -(r["score"] or 0)):
        if (r["score"] or 0) >= 7:
            print(f"  {r['score']:>4} | {r['title']} | {r['company']} | {', '.join(r['parts'])}")
            if r.get("notes"):
                print(f"       her note: {str(r['notes'])[:160]}")
    print("\n=== Applied jobs scored < 7 ===")
    for r in sorted(pos, key=lambda r: (r["score"] or 0)):
        if (r["score"] or 0) < 7:
            lb = r["labels"] or {}
            print(f"  {r['score']} | {r['title']} | {r['company']} | {', '.join(r['parts'])}")
            if lb.get("hard_fail_evidence"):
                print(f"       evidence: {lb['hard_fail_evidence'][:160]}")

    tin = sum((r["labels"] or {}).get("_usage", {}).get("input_tokens", 0) for r in rows)
    tout = sum((r["labels"] or {}).get("_usage", {}).get("output_tokens", 0) for r in rows)
    cin = sum((r["company_profile"] or {}).get("_usage", {}).get("input_tokens", 0) for r in rows if r.get("company_fresh"))
    cout = sum((r["company_profile"] or {}).get("_usage", {}).get("output_tokens", 0) for r in rows if r.get("company_fresh"))
    print(f"\nTokens — job layer: {tin} in / {tout} out over {len(rows)} jobs; "
          f"company layer (new lookups only): {cin} in / {cout} out")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pos", type=int, default=40)
    ap.add_argument("--neg", type=int, default=40)
    ap.add_argument("--german", type=int, default=25)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--job-model", default=None)
    ap.add_argument("--company-model", default=None)
    ap.add_argument("--from-results", help="Re-score a saved results file (no API calls)")
    ap.add_argument("--unapplied", action="store_true",
                    help="Score Tracker jobs she did not apply to (newest first)")
    ap.add_argument("--limit", type=int, default=None,
                    help="With --unapplied: only the N most recently added")
    args = ap.parse_args()

    os.makedirs(RESULTS_DIR, exist_ok=True)
    if args.from_results:
        with open(args.from_results, encoding="utf-8") as f:
            rows = json.load(f)
        for r in rows:
            res = ev.score_job(r, r.get("labels"), r.get("company_profile"))
            r["score"], r["parts"] = res["score"], res["parts"]
        if rows and rows[0]["label"] == "unapplied":
            report_unapplied(rows)
        else:
            report(rows)
        return

    if args.job_model:
        ev.JOB_MODEL = args.job_model
    if args.company_model:
        ev.COMPANY_MODEL = args.company_model
    print(f"Job model: {ev.JOB_MODEL} · company model: {ev.COMPANY_MODEL} · reasoning: {ev.REASONING_EFFORT}")

    cache = {}
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as f:
            cache = json.load(f)
    cached_before = set(cache)

    sample = build_unapplied(args.limit) if args.unapplied else build_sample(args.pos, args.neg, args.german, args.seed)
    print(f"Sample: {len(sample)} jobs")
    cv, rules = load_user_profile(), ev.load_matching_rules()
    if not cv:
        sys.exit("docs/user_profile.md is empty or missing")

    def work(job):
        res = run_one(job, cv, rules, cache)
        print(f"  {res['score']!s:>5} ({job['label']}) {job.get('title')} | {job.get('company')}")
        return job, res

    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        try:
            results = list(pool.map(work, sample))
        except ev.OutOfCreditsError as exc:
            pool.shutdown(wait=False, cancel_futures=True)
            sys.exit(f"Stopped: {exc}")
        for job, res in results:
            key = ev.company_key(job.get("company") or "")
            rows.append({
                "label": job["label"],
                "title": job.get("title"),
                "company": job.get("company"),
                "location": job.get("location"),
                "link": job.get("link"),
                "status": job.get("status"),
                "recommended_by": job.get("recommended_by"),
                "notes": job.get("notes"),
                "disqualification_reason": job.get("disqualification_reason"),
                "old_score": _old_score(job),
                "score": res["score"],
                "parts": res["parts"],
                "labels": res["labels"],
                "company_profile": res["company"],
                "company_fresh": bool(res["company"]) and key not in cached_before,
            })

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    prefix = "unapplied" if args.unapplied else "decisions"
    out = os.path.join(RESULTS_DIR, f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1, default=str)
    if args.unapplied:
        report_unapplied(rows)
    else:
        report(rows)
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
