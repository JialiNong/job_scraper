"""
Arbeitnow job board API — free, no key, Germany / EU tech-heavy.

https://www.arbeitnow.com/api/job-board-api returns 300 jobs per page,
newest first, full HTML description included. There is no server-side
search, so we page until listings are older than the window and let the
shared funnel filter titles and locations locally.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

from core.config import ARBEITNOW_CONFIG
from sources.base import SourceJob
from sources.http_client import get_json

SOURCE = ARBEITNOW_CONFIG["source"]


def to_source_job(item: dict) -> SourceJob:
    created = item.get("created_at")
    posted = datetime.fromtimestamp(int(created)) if created else None
    remote = bool(item.get("remote"))
    location = (item.get("location") or "").strip()
    if remote and not location:
        location = "Remote"
    return SourceJob(
        source=SOURCE,
        job_id=str(item.get("slug") or ""),
        title=(item.get("title") or "").strip(),
        company=(item.get("company_name") or "").strip(),
        location=location,
        link=item.get("url") or "",
        description=item.get("description") or "",
        posted_at=posted,
        remote=remote,
        extra={"tags": item.get("tags") or [], "job_types": item.get("job_types") or []},
    )


def fetch(days: int = 1, max_pages: int | None = None, log=print) -> List[SourceJob]:
    max_pages = max_pages or ARBEITNOW_CONFIG["max_pages"]
    cutoff = datetime.now() - timedelta(days=days)
    out: List[SourceJob] = []
    for page in range(1, max_pages + 1):
        data = get_json(ARBEITNOW_CONFIG["feed_url"], params={"page": page})
        items = data.get("data") or []
        if not items:
            break
        jobs = [to_source_job(i) for i in items]
        fresh = [j for j in jobs if j.posted_at is None or j.posted_at >= cutoff]
        out.extend(fresh)
        log(f"Arbeitnow page {page}: {len(items)} listed, {len(fresh)} within {days}d")
        if len(fresh) < len(jobs) or not (data.get("links") or {}).get("next"):
            break
    return out
