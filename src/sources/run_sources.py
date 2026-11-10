#!/usr/bin/env python3
"""
API job sources pipeline: fetch → funnel → AI match → Telegram digest.

No browser, no Chrome — runs locally or on GitHub Actions
(.github/workflows/api-sources.yml, daily cron). The LinkedIn / Indeed
pipeline (scripts/run_jobs.sh) is separate and unchanged.

Usage (from the project root):
  python3 src/sources/run_sources.py                       # Arbeitnow + Bundesagentur + ATS, last 1 day
  python3 src/sources/run_sources.py --sources arbeitnow,ats --days 3
  python3 src/sources/run_sources.py --dry-run --no-match  # print what would be saved
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from datetime import datetime

_SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sources import adzuna, arbeitnow, arbeitsagentur, ats  # noqa: E402
from core.config import API_MAX_POSTING_AGE_DAYS  # noqa: E402
from sources.base import ingest  # noqa: E402

# name → (module, keyword-less feed?)
SOURCES = {
    "arbeitnow": (arbeitnow, True),
    "arbeitsagentur": (arbeitsagentur, False),
    "adzuna": (adzuna, False),
    "ats": (ats, True),
}
# Adzuna is opt-in (--sources …,adzuna): its key is paid beyond 25 trial calls
DEFAULT_SOURCES = ["arbeitnow", "arbeitsagentur", "ats"]
SOURCE_LABELS = {
    "arbeitnow": "Arbeitnow API",
    "arbeitsagentur": "Bundesagentur",
    "adzuna": "Adzuna API",
    "ats": "ATS",
}


def _summary_line(name: str, stats: Counter, error: str | None) -> str:
    label = SOURCE_LABELS.get(name, name)
    if error and error.startswith("skipped"):
        return f"• {label}: {error}"
    if error:
        return f"⚠️ {label}: failed — {error[:120]}"
    saved = stats.get("saved", 0) + stats.get("would_save", 0)
    parts = [f"{stats.get('listed', 0)} listed", f"{saved} new saved"]
    for key, text in (
        ("known", "already seen"),
        ("cross_source_duplicate", "dup of other board"),
        ("german_filtered", "non-English"),
        ("not_germany", "outside DE"),
        ("too_old", f"posted >{API_MAX_POSTING_AGE_DAYS}d ago"),
    ):
        if stats.get(key):
            parts.append(f"{stats[key]} {text}")
    return f"• {label}: " + ", ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="API job sources pipeline")
    parser.add_argument("--sources", default=",".join(DEFAULT_SOURCES),
                        help=f"comma-separated subset of: {', '.join(SOURCES)} "
                             f"(default: {', '.join(DEFAULT_SOURCES)})")
    parser.add_argument("--days", type=int, default=1, help="posted within N days (default 1)")
    parser.add_argument("--dry-run", action="store_true", help="no DB writes, no matching, no Telegram")
    parser.add_argument("--no-match", action="store_true", help="skip AI matching")
    parser.add_argument("--no-telegram", action="store_true", help="skip Telegram summary + digest")
    args = parser.parse_args()

    names = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = [n for n in names if n not in SOURCES]
    if unknown:
        parser.error(f"unknown source(s): {', '.join(unknown)}")
    if args.days < 1:
        parser.error("--days must be ≥ 1")

    # Tag matched_jobs rows so the digest lists only this run's matches.
    # Set before importing ai_matcher, which reads it at import time.
    run_id = os.environ.get("PIPELINE_RUN_ID") or f"api_{datetime.now():%Y%m%d_%H%M%S}_{os.getpid()}"
    os.environ["PIPELINE_RUN_ID"] = run_id

    from core.db_mongo import init_db

    init_db()
    print(f"=== API sources run {run_id} · sources={','.join(names)} · days={args.days}"
          f"{' · DRY RUN' if args.dry_run else ''} ===")

    started = time.time()
    results: dict[str, tuple[Counter, str | None]] = {}
    for name in names:
        module, feed = SOURCES[name]
        print(f"\n--- {SOURCE_LABELS[name]} ---")
        if name == "adzuna" and not adzuna.credentials():
            print("Adzuna: ADZUNA_APP_ID / ADZUNA_APP_KEY not set — skipped")
            results[name] = (Counter(), "skipped — no API key (ADZUNA_APP_ID / ADZUNA_APP_KEY)")
            continue
        try:
            listings = module.fetch(days=args.days)
            stats = ingest(listings, require_on_topic_title=feed, dry_run=args.dry_run)
            results[name] = (stats, None)
            print(f"{SOURCE_LABELS[name]}: " + ", ".join(f"{k} {v}" for k, v in sorted(stats.items())))
        except Exception as exc:
            results[name] = (Counter(), f"{type(exc).__name__}: {exc}")
            print(f"✗ {SOURCE_LABELS[name]} failed: {exc}")

    if not args.dry_run and not args.no_match:
        from matching import ai_matcher

        for name in names:
            if results[name][1] is None:
                print(f"\n--- AI matching: {SOURCE_LABELS[name]} ---")
                ai_matcher.process_new_jobs(source=name)

    minutes = (time.time() - started) / 60
    summary = "\n".join(
        [f"🌐 API sources done ({minutes:.0f} min, last {args.days}d)"]
        + [_summary_line(n, *results[n]) for n in names]
    )
    print("\n" + summary)

    if not args.dry_run and not args.no_telegram:
        from tg.digest import push_todays_matched_jobs
        from tg.client import send_telegram_message

        send_telegram_message(summary)
        if not args.no_match:
            push_todays_matched_jobs(run_id=run_id)

    failed = [n for n in names if results[n][1] and not results[n][1].startswith("skipped")]
    return 1 if failed and len(failed) == len(names) else 0


if __name__ == "__main__":
    raise SystemExit(main())
