"""
Bundesagentur für Arbeit — Jobsuche API (free, public client key).

Search:  GET pc/v6/jobs?was=<keyword>&veroeffentlichtseit=<days>&pav=false&zeitarbeit=false
Detail:  GET pc/v4/jobdetails/<base64(refnr)>  → stellenangebotsBeschreibung (full JD)

Notes from probing the live API (Nov 2026):
  * v4 search is gone (403); v6 search works but has no description.
  * veroeffentlichtseit only honours 0 / 1 / 7 / 14 / 28 — other values
    silently return the unfiltered list, so we round the window up and
    also filter on datumErsteVeroeffentlichung / aenderungsdatum locally.
  * pav=false drops private recruiting agencies, zeitarbeit=false drops
    temp-staffing (Arbeitnehmerüberlassung) — roughly halves the noise.
  * Most postings are written in German; the shared funnel's language gate
    drops those after the (free) detail call, before any AI cost.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta
from typing import List

from core.config import ARBEITSAGENTUR_CONFIG, DEFAULT_KEYWORDS
from sources.base import SourceJob
from sources.http_client import get_json

SOURCE = ARBEITSAGENTUR_CONFIG["source"]
_HEADERS = {"X-API-Key": ARBEITSAGENTUR_CONFIG["api_key"]}


def published_since_param(days: int) -> int:
    """Smallest supported veroeffentlichtseit value that covers `days`."""
    for choice in ARBEITSAGENTUR_CONFIG["published_since_choices"]:
        if choice >= days:
            return choice
    return ARBEITSAGENTUR_CONFIG["published_since_choices"][-1]


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value[:19]) if "T" in value else datetime.fromisoformat(value[:10])
    except ValueError:
        return None


def _in_germany(item: dict) -> bool:
    lands = [((l.get("adresse") or {}).get("land") or "") for l in item.get("stellenlokationen") or []]
    return not lands or any(l.upper() in ("DEUTSCHLAND", "") for l in lands)


def _location(item: dict) -> str:
    for loc in item.get("stellenlokationen") or []:
        addr = loc.get("adresse") or {}
        if addr.get("ort"):
            return addr["ort"]
    return ""


def fetch_description(refnr: str) -> str:
    b64 = base64.b64encode(refnr.encode()).decode()
    data = get_json(ARBEITSAGENTUR_CONFIG["detail_url"].format(b64=b64), headers=_HEADERS)
    return data.get("stellenangebotsBeschreibung") or ""


def to_source_job(item: dict) -> SourceJob:
    refnr = item.get("referenznummer") or ""
    remote = bool(item.get("homeofficemoeglich"))
    location = _location(item)
    if remote:
        location = f"{location} (home office possible)" if location else "Remote"
    return SourceJob(
        source=SOURCE,
        job_id=refnr,
        title=(item.get("stellenangebotsTitel") or "").strip(),
        company=(item.get("firma") or "").strip(),
        location=location,
        link=ARBEITSAGENTUR_CONFIG["job_url"].format(refnr=refnr),
        posted_at=_parse_date(item.get("datumErsteVeroeffentlichung")),
        remote=remote,
        extra={
            k: v
            for k, v in {
                "external_url": item.get("externeURL") or "",
                "salary_from": item.get("gehaltsspanneVon"),
                "salary_to": item.get("gehaltsspanneBis"),
            }.items()
            if v
        },
        fetch_description=lambda r=refnr: fetch_description(r),
        location_policy="none",  # filtered on adresse.land in fetch()
    )


def _is_fresh(item: dict, cutoff: datetime) -> bool:
    dates = [
        _parse_date(item.get("datumErsteVeroeffentlichung")),
        _parse_date(item.get("aenderungsdatum")),
    ]
    dates = [d for d in dates if d]
    return not dates or max(dates) >= cutoff


def fetch(days: int = 1, keywords=None, max_pages: int | None = None, log=print) -> List[SourceJob]:
    keywords = keywords or DEFAULT_KEYWORDS
    max_pages = max_pages or ARBEITSAGENTUR_CONFIG["max_pages_per_keyword"]
    size = ARBEITSAGENTUR_CONFIG["page_size"]
    since = published_since_param(days)
    # Local cutoff: start of the day `days` ago (API dates are day-granular)
    cutoff = (datetime.now() - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    out: List[SourceJob] = []
    for keyword in keywords:
        for page in range(1, max_pages + 1):
            data = get_json(
                ARBEITSAGENTUR_CONFIG["search_url"],
                params={
                    "was": keyword,
                    "angebotsart": 1,  # regular employment (not apprenticeship / self-employed)
                    "veroeffentlichtseit": since,
                    "pav": "false",
                    "zeitarbeit": "false",
                    "size": size,
                    "page": page,
                },
                headers=_HEADERS,
            )
            items = data.get("ergebnisliste") or []
            fresh = [i for i in items if _is_fresh(i, cutoff) and _in_germany(i)]
            out.extend(to_source_job(i) for i in fresh)
            total = data.get("maxErgebnisse") or 0
            log(f"Arbeitsagentur '{keyword}' page {page}: {len(items)}/{total}, {len(fresh)} within {days}d")
            if page * size >= total or len(items) < size:
                break
    return out
