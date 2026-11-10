"""
Adzuna job search API — OPT-IN, needs ADZUNA_APP_ID / ADZUNA_APP_KEY.

Not free in practice: a new key is "Pending Activation" with 25 test calls;
5,000 calls/month needs the paid plan (£8/month, Nov 2026). One daily run
uses 6–12 calls (6 keywords × 1–2 pages), so it is not in the default
source list — run it with `--sources …,adzuna`. Without keys it is skipped.

Adzuna's `description` is truncated (~500 chars), which is too short for
the matcher. For each listing that survives the free filters we follow
`redirect_url` to the original posting and extract the main text with
trafilatura; if that still yields a short teaser the job is saved with
description_incomplete=True and the matcher skips it (same as an
incomplete LinkedIn JD), so it costs no AI call.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import List

from core.config import ADZUNA_CONFIG, DEFAULT_KEYWORDS
from sources.base import SourceJob
from sources.http_client import get_json, get_text

SOURCE = ADZUNA_CONFIG["source"]


def credentials():
    app_id = os.getenv("ADZUNA_APP_ID", "").strip()
    app_key = os.getenv("ADZUNA_APP_KEY", "").strip()
    return (app_id, app_key) if app_id and app_key else None


def fetch_full_description(url: str, fallback: str) -> str:
    """Follow the Adzuna redirect and extract the posting body; fall back to the teaser."""
    try:
        import trafilatura  # optional dependency (in requirements.txt)

        html = get_text(url, headers={"Accept": "text/html"}, retries=1)
        text = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
        if len(text) > len(fallback):
            return text
    except Exception:
        pass
    return fallback


def to_source_job(item: dict) -> SourceJob:
    created = item.get("created")
    posted = None
    if created:
        try:
            posted = datetime.fromisoformat(created.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            posted = None
    teaser = item.get("description") or ""
    url = item.get("redirect_url") or ""
    return SourceJob(
        source=SOURCE,
        job_id=str(item.get("id") or ""),
        title=(item.get("title") or "").strip(),
        company=((item.get("company") or {}).get("display_name") or "").strip(),
        location=((item.get("location") or {}).get("display_name") or "").strip(),
        link=url,
        posted_at=posted,
        extra={
            k: v
            for k, v in {
                "salary_from": item.get("salary_min"),
                "salary_to": item.get("salary_max"),
                "contract_type": item.get("contract_type"),
            }.items()
            if v
        },
        fetch_description=lambda u=url, t=teaser: fetch_full_description(u, t),
        location_policy="none",  # /jobs/de/ endpoint: Germany only
    )


def fetch(days: int = 1, keywords=None, max_pages: int | None = None, log=print) -> List[SourceJob]:
    creds = credentials()
    if not creds:
        log("Adzuna: ADZUNA_APP_ID / ADZUNA_APP_KEY not set — skipped (paid key: developer.adzuna.com)")
        return []
    app_id, app_key = creds
    keywords = keywords or DEFAULT_KEYWORDS
    max_pages = max_pages or ADZUNA_CONFIG["max_pages_per_keyword"]
    per_page = ADZUNA_CONFIG["results_per_page"]
    out: List[SourceJob] = []
    for keyword in keywords:
        for page in range(1, max_pages + 1):
            data = get_json(
                ADZUNA_CONFIG["search_url"].format(page=page),
                params={
                    "app_id": app_id,
                    "app_key": app_key,
                    "what_phrase": keyword,
                    "max_days_old": days,
                    "results_per_page": per_page,
                    "sort_by": "date",
                },
            )
            items = data.get("results") or []
            out.extend(to_source_job(i) for i in items)
            total = data.get("count") or 0
            log(f"Adzuna '{keyword}' page {page}: {len(items)}/{total}")
            if page * per_page >= total or len(items) < per_page:
                break
    return out
