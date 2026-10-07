#!/usr/bin/env python3
"""
Dry-run matching eval against a frozen golden JD set.

Reuses the same German / stack / AI-ML gates + AI matcher as production, but NEVER
writes to MongoDB (no mark_job_as_matched / matched_jobs).

Usage:
  python3 scripts/eval_matcher.py                 # run all cases
  python3 scripts/eval_matcher.py -l 3            # smoke test
  python3 scripts/eval_matcher.py --ids german-mandatory-junior-swe,devops-title-should-fail
  python3 scripts/eval_matcher.py --save-baseline # freeze this run as "old matcher"
  python3 scripts/eval_matcher.py --baseline evals/baselines/latest.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from dotenv import load_dotenv

from matching.ai_matcher import (
    evaluate_job,
    load_matching_criteria,
    load_user_profile,
)

load_dotenv(os.path.join(_ROOT, ".env"))

DEFAULT_FIXTURES = os.path.join(_ROOT, "evals", "matching_eval_set.json")
DEFAULT_BASELINE = os.path.join(_ROOT, "evals", "baselines", "latest.json")
RESULTS_DIR = os.path.join(_ROOT, "evals", "results")

BANDS: Dict[str, Tuple[float, float]] = {
    "fail": (0.0, 3.0),
    "weak": (4.0, 6.0),
    "good": (7.0, 8.0),
    "special": (9.0, 10.0),
}


def score_to_band(score: float, special_match: bool = False) -> str:
    if special_match or score >= 9.0:
        return "special"
    if score <= 3.0:
        return "fail"
    if score <= 6.0:
        return "weak"
    return "good"


def distance_to_band(score: float, band: str) -> float:
    lo, hi = BANDS[band]
    if lo <= score <= hi:
        return 0.0
    if score < lo:
        return lo - score
    return score - hi


def load_fixtures(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not data.get("cases"):
        raise SystemExit(f"No cases in {path}")
    return data


def load_baseline(path: Optional[str]) -> Dict[str, Dict[str, Any]]:
    """Map eval_id -> {match_score, recommendation, special_match, ...}."""
    if not path:
        return {}
    if not os.path.exists(path):
        print(f"Warning: baseline not found at {path}; comparing to fixture baseline_from_db only")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cases = data.get("cases") or data.get("results") or []
    out = {}
    for row in cases:
        eid = row.get("eval_id")
        if not eid:
            continue
        # Saved baseline format from --save-baseline
        if "match_score" in row:
            out[eid] = row
        elif "new" in row:
            out[eid] = row["new"]
    return out


def baseline_for_case(
    case: Dict[str, Any],
    file_baseline: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    eid = case["eval_id"]
    if eid in file_baseline:
        return file_baseline[eid]
    db_base = case.get("baseline_from_db") or {}
    if db_base.get("match_score") is not None:
        return {
            "match_score": db_base.get("match_score"),
            "recommendation": db_base.get("recommendation"),
            "special_match": bool(db_base.get("special_match")),
            "disqualification_reason": db_base.get("disqualification_reason") or "",
            "summary": db_base.get("summary") or "",
            "source": "fixture_db",
        }
    return None


def run_one(
    case: Dict[str, Any],
    user_profile: str,
    criteria: str,
    temperature: float,
) -> Dict[str, Any]:
    job = dict(case["job"])
    analysis = evaluate_job(
        job, user_profile, criteria, temperature=temperature
    )
    if not analysis:
        return {
            "eval_id": case["eval_id"],
            "expected_band": case["expected_band"],
            "error": "AI analysis failed",
            "used_german_gate": False,
            "match_gate": "",
        }

    match_gate = analysis.get("match_gate") or "scored"
    used_german_gate = match_gate == "german"

    score = float(analysis.get("match_score") or 0)
    special = bool(analysis.get("special_match"))
    actual_band = score_to_band(score, special)

    return {
        "eval_id": case["eval_id"],
        "expected_band": case["expected_band"],
        "note": case.get("note") or "",
        "title": job.get("title") or "",
        "company": job.get("company") or "",
        "location": job.get("location") or "",
        "warnings": list(case.get("warnings") or []),
        "used_german_gate": used_german_gate,
        "match_gate": match_gate,
        "match_score": score,
        "recommendation": analysis.get("recommendation") or "",
        "special_match": special,
        "special_match_reasons": analysis.get("special_match_reasons") or [],
        "disqualification_reason": analysis.get("disqualification_reason") or "",
        "summary": analysis.get("summary") or "",
        "actual_band": actual_band,
        "band_pass": actual_band == case["expected_band"],
        "distance_to_expected": distance_to_band(score, case["expected_band"]),
    }


def compare_to_baseline(row: Dict[str, Any], baseline: Optional[Dict[str, Any]]) -> None:
    if not baseline or baseline.get("match_score") is None:
        row["baseline_score"] = None
        row["delta"] = None
        row["vs_baseline"] = "n/a"
        return

    old = float(baseline["match_score"])
    new = float(row["match_score"])
    delta = round(new - old, 2)
    old_dist = distance_to_band(old, row["expected_band"])
    new_dist = row["distance_to_expected"]

    special_changed = bool(baseline.get("special_match")) != bool(
        row.get("special_match")
    )

    if new_dist < old_dist - 0.05:
        # Closer to the expected band (e.g. 8 → 2 when expected fail)
        vs = "improved"
    elif new_dist > old_dist + 0.05:
        # Farther from the expected band (e.g. 2 → 7 when expected fail)
        vs = "regress"
    elif abs(delta) < 0.05 and not special_changed:
        vs = "same"
    else:
        # Still the same distance (usually both already inside the expected
        # band). Treat in-band score jitter as same, not regress/improved.
        vs = "same"

    row["baseline_score"] = old
    row["baseline_special_match"] = bool(baseline.get("special_match"))
    row["baseline_source"] = baseline.get("source") or "file"
    row["delta"] = delta
    row["vs_baseline"] = vs


def fmt_score(v: Any) -> str:
    if v is None:
        return "  - "
    return f"{float(v):4.1f}"


def print_table(rows: List[Dict[str, Any]]) -> None:
    header = (
        f"{'eval_id':<34} {'exp':<8} {'base':>5} {'new':>5} {'Δ':>5} "
        f"{'band':<8} {'vs':<9} {'ok':<5} title"
    )
    print("\n" + header)
    print("-" * len(header) + "-" * 24)

    for r in rows:
        if r.get("error"):
            print(
                f"{r['eval_id']:<34} {r.get('expected_band','?'):<8} "
                f"{'ERR':>5} {'':>5} {'':>5} {'':<8} {'':<9} {'FAIL':<5} {r['error']}"
            )
            continue

        delta = r.get("delta")
        delta_s = "  - " if delta is None else f"{delta:+4.1f}"
        ok = "PASS" if r.get("band_pass") else "MISS"
        warn = " !" if r.get("warnings") else ""
        gate = ""
        if r.get("used_german_gate"):
            gate = " [DE]"
        elif r.get("match_gate") == "stack":
            gate = " [ST]"
        elif r.get("match_gate") == "ai_ml":
            gate = " [ML]"
        title = (r.get("title") or "")[:40]
        print(
            f"{r['eval_id']:<34} {r['expected_band']:<8} "
            f"{fmt_score(r.get('baseline_score'))} {fmt_score(r.get('match_score'))} "
            f"{delta_s:>5} {r.get('actual_band', '?'):<8} "
            f"{r.get('vs_baseline', 'n/a'):<9} {ok:<5} {title}{gate}{warn}"
        )


def print_summary(rows: List[Dict[str, Any]]) -> int:
    usable = [r for r in rows if not r.get("error")]
    errors = [r for r in rows if r.get("error")]
    passed = [r for r in usable if r.get("band_pass")]
    missed = [r for r in usable if not r.get("band_pass")]
    improved = [r for r in usable if r.get("vs_baseline") == "improved"]
    regress = [r for r in usable if r.get("vs_baseline") == "regress"]

    print("\n=== Summary ===")
    print(f"Cases:     {len(rows)}")
    print(f"Band PASS: {len(passed)}/{len(usable)}")
    print(f"Band MISS: {len(missed)}")
    print(f"Improved:  {len(improved)}")
    print(f"Regress:   {len(regress)}")
    print(f"Errors:    {len(errors)}")

    if missed:
        print("\nMisses:")
        for r in missed:
            print(
                f"  · {r['eval_id']}: expected {r['expected_band']}, "
                f"got {r.get('actual_band')} ({r.get('match_score')}) "
                f"— {r.get('summary', '')[:100]}"
            )
    if regress:
        print("\nRegressions vs baseline:")
        for r in regress:
            print(
                f"  · {r['eval_id']}: {r.get('baseline_score')} → {r.get('match_score')} "
                f"(Δ {r.get('delta'):+}) expected {r['expected_band']}"
            )
    if improved:
        print("\nImprovements vs baseline:")
        for r in improved:
            print(
                f"  · {r['eval_id']}: {r.get('baseline_score')} → {r.get('match_score')} "
                f"(Δ {r.get('delta'):+})"
            )

    # Non-zero exit if band misses, regressions, or errors
    if errors or missed or regress:
        return 1
    return 0


def save_baseline(path: str, rows: List[Dict[str, Any]], meta: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "meta": meta,
        "cases": [
            {
                "eval_id": r["eval_id"],
                "match_score": r.get("match_score"),
                "recommendation": r.get("recommendation"),
                "special_match": r.get("special_match"),
                "special_match_reasons": r.get("special_match_reasons"),
                "disqualification_reason": r.get("disqualification_reason"),
                "summary": r.get("summary"),
                "actual_band": r.get("actual_band"),
                "used_german_gate": r.get("used_german_gate"),
                "match_gate": r.get("match_gate"),
            }
            for r in rows
            if not r.get("error")
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"\n✓ Baseline saved: {path} ({len(payload['cases'])} cases)")


def save_results(rows: List[Dict[str, Any]], meta: Dict[str, Any]) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(RESULTS_DIR, f"run_{stamp}.json")
    payload = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "meta": meta,
        "results": rows,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"✓ Results saved: {path}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run AI matcher against frozen eval JDs (no DB writes)"
    )
    parser.add_argument(
        "--fixtures",
        "-f",
        default=DEFAULT_FIXTURES,
        help="Path to matching_eval_set.json",
    )
    parser.add_argument(
        "--baseline",
        "-b",
        default=None,
        help="Baseline JSON from a previous --save-baseline (default: evals/baselines/latest.json if present, else fixture DB scores)",
    )
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help=f"Write this run to {DEFAULT_BASELINE}",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=None,
        help="Only run the first N cases",
    )
    parser.add_argument(
        "--ids",
        default=None,
        help="Comma-separated eval_id list to run",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="AI temperature for eval (default 0.0 for more stable scores)",
    )
    parser.add_argument(
        "--no-save-results",
        action="store_true",
        help="Do not write evals/results/run_*.json",
    )
    args = parser.parse_args()

    fixtures = load_fixtures(args.fixtures)
    cases = list(fixtures["cases"])

    if args.ids:
        wanted = {x.strip() for x in args.ids.split(",") if x.strip()}
        cases = [c for c in cases if c["eval_id"] in wanted]
        missing = wanted - {c["eval_id"] for c in cases}
        if missing:
            print(f"Warning: unknown eval_ids: {sorted(missing)}")

    if args.limit is not None:
        cases = cases[: args.limit]

    baseline_path = args.baseline
    if baseline_path is None and os.path.exists(DEFAULT_BASELINE):
        baseline_path = DEFAULT_BASELINE
    file_baseline = load_baseline(baseline_path)

    user_profile = load_user_profile()
    if not user_profile:
        print("Error: docs/user_profile.md missing or empty")
        return 2
    criteria = load_matching_criteria()
    if not criteria:
        print("Warning: docs/matching_criteria.md missing; continuing with profile only")

    print("=== Matching Eval (dry-run, no DB writes) ===")
    print(f"Fixtures:  {args.fixtures} ({len(cases)} cases)")
    print(f"Baseline:  {baseline_path or 'fixture baseline_from_db'}")
    print(f"Model:     {os.getenv('AI_MODEL', 'gpt-4.1-mini')}")
    print(f"Temp:      {args.temperature}")

    rows: List[Dict[str, Any]] = []
    for i, case in enumerate(cases, 1):
        print(f"\n--- [{i}/{len(cases)}] {case['eval_id']} ---")
        print(f"Expected: {case['expected_band']} | {case.get('note', '')[:90]}")
        if case.get("warnings"):
            print(f"⚠ warnings: {case['warnings']}")

        row = run_one(case, user_profile, criteria, temperature=args.temperature)
        compare_to_baseline(row, baseline_for_case(case, file_baseline))
        rows.append(row)

        if row.get("error"):
            print(f"✗ {row['error']}")
            continue
        print(
            f"Score: {row['match_score']}/10  band={row['actual_band']}  "
            f"expected={row['expected_band']}  "
            f"{'PASS' if row['band_pass'] else 'MISS'}  "
            f"vs_baseline={row['vs_baseline']}"
            + (f" (Δ {row['delta']:+})" if row.get("delta") is not None else "")
        )
        if row.get("used_german_gate"):
            print(f"German gate: {row.get('disqualification_reason', '')[:120]}")
        elif row.get("match_gate") in ("stack", "ai_ml"):
            print(
                f"{row['match_gate']} gate: "
                f"{row.get('disqualification_reason', '')[:120]}"
            )
        if row.get("special_match"):
            reasons = row.get("special_match_reasons") or []
            print(f"★ Special Match: {', '.join(reasons[:3])}")
        if row.get("summary"):
            print(f"Summary: {row['summary'][:160]}")

    print_table(rows)

    meta = {
        "fixtures": args.fixtures,
        "baseline": baseline_path,
        "model": os.getenv("AI_MODEL", "gpt-4.1-mini"),
        "temperature": args.temperature,
        "case_count": len(cases),
    }

    if not args.no_save_results:
        save_results(rows, meta)

    if args.save_baseline:
        save_baseline(DEFAULT_BASELINE, rows, meta)

    return print_summary(rows)


if __name__ == "__main__":
    raise SystemExit(main())
