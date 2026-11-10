"""
Company ATS boards — official public JSON/XML job feeds, no key needed.

These endpoints list one company's open roles, so they need a watchlist:
config/ats_companies.json, entries like

    {"company": "n8n", "provider": "ashby", "slug": "n8n"}

Find a company's provider + slug with scripts/tools/discover_ats.py.
Every run fetches all open roles of every watched company; already-saved
ones are skipped by job_id dedup, so only new openings reach the funnel.

Providers:
  greenhouse  boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
  lever       api.lever.co/v0/postings/{slug}?mode=json   (lever_eu: api.eu.lever.co)
  ashby       api.ashbyhq.com/posting-api/job-board/{slug}
  personio    {slug}.jobs.personio.de/xml                 (host override: jobs.personio.com)
  recruitee   {slug}.recruitee.com/api/offers/
  workable    apply.workable.com/api/v1/widget/accounts/{slug}?details=true
"""
from __future__ import annotations

import html
import json
import os
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Callable, Dict, List

from core.config import ATS_CONFIG
from sources.base import SourceJob
from sources.http_client import get_json, get_text

SOURCE = ATS_CONFIG["source"]
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _dt(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):  # Lever: epoch milliseconds
            return datetime.fromtimestamp(value / 1000)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, OSError):
        return None


def _join(*parts) -> str:
    seen, out = set(), []
    for p in parts:
        p = (p or "").strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return " · ".join(out)


def _job(entry: dict, provider: str, raw_id, **kw) -> SourceJob:
    slug = entry["slug"]
    return SourceJob(
        source=SOURCE,
        job_id=f"{provider}:{slug}:{raw_id}",
        company=entry.get("company") or slug,
        extra={"ats_provider": provider, "ats_slug": slug},
        location_policy="strict",  # global boards: keep only DE / unscoped remote
        **kw,
    )


# ---------- providers ----------

def fetch_greenhouse(entry: dict) -> List[SourceJob]:
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{entry['slug']}/jobs", params={"content": "true"})
    out = []
    for j in data.get("jobs") or []:
        out.append(_job(
            entry, "greenhouse", j.get("id"),
            title=j.get("title") or "",
            location=(j.get("location") or {}).get("name") or "",
            link=j.get("absolute_url") or "",
            description=html.unescape(j.get("content") or ""),
            posted_at=_dt(j.get("first_published") or j.get("updated_at")),
        ))
    return out


def _fetch_lever(entry: dict, host: str, provider: str) -> List[SourceJob]:
    data = get_json(f"https://{host}/v0/postings/{entry['slug']}", params={"mode": "json"})
    out = []
    for j in data if isinstance(data, list) else []:
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or [cats.get("location")]
        remote = (j.get("workplaceType") or "").lower() == "remote"
        lists = "\n".join(
            f"{li.get('text', '')}\n{li.get('content', '')}" for li in j.get("lists") or []
        )
        body = "\n\n".join(
            p for p in (j.get("descriptionPlain") or j.get("description"), lists,
                        j.get("additionalPlain") or j.get("additional")) if p
        )
        out.append(_job(
            entry, provider, j.get("id"),
            title=j.get("text") or "",
            location=_join(*locs, "Remote" if remote else "", j.get("country")),
            link=j.get("hostedUrl") or "",
            description=body,
            posted_at=_dt(j.get("createdAt")),
            remote=remote,
        ))
    return out


def fetch_lever(entry):
    return _fetch_lever(entry, "api.lever.co", "lever")


def fetch_lever_eu(entry):
    return _fetch_lever(entry, "api.eu.lever.co", "lever_eu")


def fetch_ashby(entry: dict) -> List[SourceJob]:
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{entry['slug']}")
    out = []
    for j in data.get("jobs") or []:
        if j.get("isListed") is False:
            continue
        country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")) or ""
        secondary = [s.get("location") for s in j.get("secondaryLocations") or []]
        remote = bool(j.get("isRemote")) or (j.get("workplaceType") or "").lower() == "remote"
        out.append(_job(
            entry, "ashby", j.get("id"),
            title=j.get("title") or "",
            location=_join(j.get("location"), *secondary, country, "Remote" if remote else ""),
            link=j.get("jobUrl") or "",
            description=j.get("descriptionHtml") or j.get("descriptionPlain") or "",
            posted_at=_dt(j.get("publishedAt")),
            remote=remote,
        ))
    return out


def fetch_personio(entry: dict) -> List[SourceJob]:
    host = entry.get("host") or "jobs.personio.de"
    base = f"https://{entry['slug']}.{host}"
    root = ET.fromstring(get_text(f"{base}/xml", params={"language": "en"}, headers={"Accept": "application/xml"}))
    out = []
    for pos in root.findall("position"):
        pid = (pos.findtext("id") or "").strip()
        offices = [pos.findtext("office") or ""] + [
            o.text or "" for o in pos.findall("additionalOffices/office")
        ]
        sections = []
        for d in pos.findall("jobDescriptions/jobDescription"):
            name = (d.findtext("name") or "").strip()
            value = d.findtext("value") or ""
            sections.append(f"<h3>{html.escape(name)}</h3>{value}" if name else value)
        out.append(_job(
            entry, "personio", pid,
            title=(pos.findtext("name") or "").strip(),
            location=_join(*offices),
            link=f"{base}/job/{pid}?language=en",
            description="\n".join(sections),
            posted_at=_dt(pos.findtext("createdAt")),
        ))
    return out


def fetch_recruitee(entry: dict) -> List[SourceJob]:
    data = get_json(f"https://{entry['slug']}.recruitee.com/api/offers/")
    out = []
    for j in data.get("offers") or []:
        if j.get("status") and j.get("status") != "published":
            continue
        remote = bool(j.get("remote"))
        out.append(_job(
            entry, "recruitee", j.get("id"),
            title=j.get("title") or "",
            location=_join(j.get("location"), j.get("city"), j.get("country"), "Remote" if remote else ""),
            link=j.get("careers_url") or j.get("url") or "",
            description=f"{j.get('description') or ''}\n{j.get('requirements') or ''}",
            posted_at=_dt(j.get("published_at") or j.get("created_at")),
            remote=remote,
        ))
    return out


def fetch_workable(entry: dict) -> List[SourceJob]:
    data = get_json(
        f"https://apply.workable.com/api/v1/widget/accounts/{entry['slug']}", params={"details": "true"}
    )
    out = []
    for j in data.get("jobs") or []:
        remote = bool(j.get("telecommuting"))
        locs = [
            _join(l.get("city"), l.get("country")) for l in j.get("locations") or []
        ] or [_join(j.get("city"), j.get("country"))]
        out.append(_job(
            entry, "workable", j.get("shortcode") or j.get("id"),
            title=j.get("title") or "",
            location=_join(*locs, "Remote" if remote else ""),
            link=j.get("url") or j.get("shortlink") or "",
            description=j.get("description") or "",
            posted_at=_dt(j.get("published_on") or j.get("created_at")),
            remote=remote,
        ))
    return out


PROVIDERS: Dict[str, Callable[[dict], List[SourceJob]]] = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "lever_eu": fetch_lever_eu,
    "ashby": fetch_ashby,
    "personio": fetch_personio,
    "recruitee": fetch_recruitee,
    "workable": fetch_workable,
}


# ---------- watchlist ----------

def watchlist_path() -> str:
    path = ATS_CONFIG["watchlist_path"]
    return path if os.path.isabs(path) else os.path.join(_PROJECT_ROOT, path)


def load_watchlist(path: str | None = None) -> List[dict]:
    path = path or watchlist_path()
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    entries = data.get("companies", []) if isinstance(data, dict) else data
    return [
        e for e in entries
        if e.get("slug") and e.get("provider") in PROVIDERS and e.get("enabled", True)
    ]


def fetch(days: int = 1, watchlist: List[dict] | None = None, log=print) -> List[SourceJob]:
    """All open roles of every watched company (`days` is ignored: boards list only open roles)."""
    entries = watchlist if watchlist is not None else load_watchlist()
    if not entries:
        log(f"ATS: watchlist empty ({watchlist_path()}) — skipped")
        return []

    def one(entry):
        try:
            return entry, PROVIDERS[entry["provider"]](entry), None
        except Exception as exc:
            return entry, [], exc

    out: List[SourceJob] = []
    failed = 0
    with ThreadPoolExecutor(max_workers=ATS_CONFIG["max_workers"]) as pool:
        for entry, jobs, exc in pool.map(one, entries):
            name = f"{entry.get('company')} ({entry['provider']}:{entry['slug']})"
            if exc:
                failed += 1
                log(f"ATS {name}: failed — {exc}")
                continue
            out.extend(j for j in jobs if j.title and j.link)
    log(f"ATS: {len(entries)} companies, {len(out)} open roles, {failed} board(s) failed")
    return out
