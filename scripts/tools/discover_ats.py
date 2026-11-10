#!/usr/bin/env python3
"""
Find which ATS a company uses (and its board slug) for config/ats_companies.json.

Tries slug guesses ("Buying Labs GmbH" → buyinglabs, buying-labs, buying)
against every provider in src/sources/ats.py and reports boards that list
at least one open role (several providers answer 200 + empty list for any
slug, so an empty board is never counted as a hit).

Usage:
  python3 scripts/tools/discover_ats.py "Personio" "n8n" "Buying Labs GmbH"
  python3 scripts/tools/discover_ats.py --from-tracker --limit 150      # Tracker companies
  python3 scripts/tools/discover_ats.py "n8n" --write                    # add hits to the watchlist

--from-tracker reads matched_jobs company names (read-only) and skips
MATCH_BLOCKED_COMPANIES. Review hits before --write: a short slug can
belong to a different company with the same name.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

from core.dedup import normalize_company  # noqa: E402
from sources import ats  # noqa: E402
from sources.http_client import get_json  # noqa: E402

# Recruiters / job boards / big corps on their own career systems: never an ATS board
_SKIP = re.compile(
    r"recruit|staffing|talent|jobgether|instaffo|hiredbuddy|michael page|harvey nash|"
    r"agile professionals|indeed|amazon|google|sap\b|accenture|infosys|hcltech|siemens|airbus",
    re.IGNORECASE,
)


def slug_candidates(company: str) -> list[str]:
    core = normalize_company(company)
    words = [w for w in re.split(r"[^a-z0-9]+", core.replace("ä", "ae").replace("ö", "oe")
                                 .replace("ü", "ue").replace("ß", "ss")) if w]
    if not words:
        return []
    cands = ["".join(words), "-".join(words)]
    if len(words) > 1 and len(words[0]) >= 4:
        cands.append(words[0])
    raw = re.sub(r"[^a-z0-9]+", "", company.lower())
    cands.append(raw)
    out = []
    for c in cands:
        if c and c not in out and len(c) >= 2:
            out.append(c)
    return out


def board_name(provider: str, slug: str) -> str | None:
    """Company name the board reports, when the provider exposes one."""
    try:
        if provider == "greenhouse":
            return get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}").get("name")
        if provider == "workable":
            return get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}").get("name")
        if provider == "recruitee":
            offers = get_json(f"https://{slug}.recruitee.com/api/offers/").get("offers") or []
            return offers[0].get("company_name") if offers else None
    except Exception:
        return None
    return None


def names_match(a: str, b: str) -> bool:
    x, y = normalize_company(a).replace(" ", ""), normalize_company(b).replace(" ", "")
    return bool(x and y) and (x in y or y in x)


def probe(company: str, provider: str, slug: str):
    entry = {"company": company, "provider": provider, "slug": slug}
    try:
        jobs = ats.PROVIDERS[provider](entry)
    except Exception:
        return None
    if not jobs:
        return None
    reported = board_name(provider, slug)
    if reported and not names_match(reported, company):
        return None  # same slug, different company ("scalable" → The Scalable Company)
    full_slug = slug in slug_candidates(company)[:2] or slug == re.sub(r"[^a-z0-9]+", "", company.lower())
    verified = bool(reported) or full_slug
    if not verified:
        return None  # first-word-only guess with no name to confirm it
    from core.db_mongo import is_germany_or_remote

    de = sum(1 for j in jobs if not j.location or is_germany_or_remote(j.location))
    return {**entry, "open_roles": len(jobs), "germany_or_remote_roles": de,
            "board_name": reported or ""}


def discover(companies: list[str], workers: int = 16) -> list[dict]:
    tasks = []
    for company in companies:
        for slug in slug_candidates(company):
            for provider in ats.PROVIDERS:
                tasks.append((company, provider, slug))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = [r for r in pool.map(lambda t: probe(*t), tasks) if r]
    # Keep the best hit per (company, provider): most open roles
    best: dict[tuple[str, str], dict] = {}
    for r in results:
        k = (r["company"], r["provider"].replace("_eu", ""))
        if k not in best or r["open_roles"] > best[k]["open_roles"]:
            best[k] = r
    return sorted(best.values(), key=lambda r: (r["company"].lower(), r["provider"]))


def tracker_companies(limit: int) -> list[str]:
    from core.config import MATCH_BLOCKED_COMPANIES
    from core.db_mongo import get_collection

    counts: Counter = Counter()
    names: dict[str, str] = {}
    for row in get_collection("matched_jobs").find({}, {"company": 1}):
        name = (row.get("company") or "").strip()
        key = normalize_company(name)
        if not key or _SKIP.search(name):
            continue
        if any(b.lower() in name.lower() for b in MATCH_BLOCKED_COMPANIES):
            continue
        counts[key] += 1
        names.setdefault(key, re.sub(r"\s*\(.*?\)\s*", " ", name).strip())
    return [names[k] for k, _ in counts.most_common(limit)]


def write_watchlist(hits: list[dict]) -> int:
    path = ats.watchlist_path()
    data = {"companies": []}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    existing = {(e["provider"], e["slug"]) for e in data["companies"]}
    added = 0
    for h in hits:
        if (h["provider"], h["slug"]) in existing or not h["germany_or_remote_roles"]:
            continue
        data["companies"].append({"company": h["company"], "provider": h["provider"], "slug": h["slug"]})
        existing.add((h["provider"], h["slug"]))
        added += 1
    data["companies"].sort(key=lambda e: e["company"].lower())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return added


def main() -> int:
    parser = argparse.ArgumentParser(description="Find company ATS boards")
    parser.add_argument("companies", nargs="*")
    parser.add_argument("--from-tracker", action="store_true", help="use Tracker company names")
    parser.add_argument("--limit", type=int, default=150, help="Tracker companies to try")
    parser.add_argument("--write", action="store_true", help="add hits with DE/remote roles to the watchlist")
    parser.add_argument("--json", action="store_true", help="print hits as JSON")
    args = parser.parse_args()

    companies = list(args.companies)
    if args.from_tracker:
        companies += tracker_companies(args.limit)
    if not companies:
        parser.error("give company names or --from-tracker")

    print(f"Probing {len(companies)} companies × {len(ats.PROVIDERS)} providers…", file=sys.stderr)
    hits = discover(companies)
    if args.json:
        print(json.dumps(hits, indent=2, ensure_ascii=False))
    else:
        for h in hits:
            print(f"{h['company']:<40} {h['provider']:<11} {h['slug']:<28} "
                  f"{h['open_roles']:>4} open · {h['germany_or_remote_roles']:>3} DE/remote")
        found = {h["company"] for h in hits}
        missing = [c for c in companies if c not in found]
        if missing:
            print(f"\nNo public board found ({len(missing)}): " + ", ".join(missing), file=sys.stderr)
    if args.write:
        print(f"\nAdded {write_watchlist(hits)} board(s) to {ats.watchlist_path()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
