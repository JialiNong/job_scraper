"""
Parse and normalise job URLs pasted into Log Job / the manual logger.

Kept free of Playwright so URL handling can be unit-tested without Chrome.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional
from urllib.parse import urlparse, urlunparse

from core.scraper_utils import extract_indeed_jk_from_url

_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_TRAILING_JUNK_RE = re.compile(r"[.,;:)\]\"']+$")
# /jobs/view/4361502751/ and the slug form agents share:
# /jobs/view/frontend-engineer-all-genders-at-avelios-medical-4361502751/
_LINKEDIN_VIEW_RE = re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d+)(?=[/?#]|$)")
_LINKEDIN_CURRENT_RE = re.compile(r"[?&]currentJobId=(\d+)")


def normalize_pasted_job_url(url: str) -> str:
    """
    Clean a pasted job URL so Indeed / LinkedIn ids still resolve.

    Strips wrapping quotes, trailing punctuation, and adds https:// when
    the user pasted `de.indeed.com/viewjob?jk=…` without a scheme.
    """
    text = (url or "").strip()
    if not text or text.startswith("#"):
        return ""
    text = text.strip("<> \t")
    text = _TRAILING_JUNK_RE.sub("", text)
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        text = text[1:-1].strip()
    elif text[:1] in "\"'":
        text = text[1:].strip()
        text = _TRAILING_JUNK_RE.sub("", text)
    if not text:
        return ""
    if text.startswith("//"):
        text = "https:" + text
    elif not _SCHEME_RE.match(text):
        text = "https://" + text
    return text


def detect_source(url: str) -> str:
    """Return 'linkedin', 'indeed', or 'manual' based on the hostname."""
    url_lower = normalize_pasted_job_url(url).lower()
    if "linkedin.com" in url_lower:
        return "linkedin"
    if "indeed.com" in url_lower:
        return "indeed"
    return "manual"


def canonicalize_url(url: str) -> str:
    """Strip fragment and trailing slash for stable job_id hashing."""
    parsed = urlparse(normalize_pasted_job_url(url))
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", parsed.query, ""))


def job_id_from_url(url: str) -> str:
    """Stable short id derived from the canonical URL (non-LinkedIn/Indeed)."""
    return hashlib.sha1(canonicalize_url(url).encode("utf-8")).hexdigest()[:16]


def extract_linkedin_job_id(url: str) -> Optional[str]:
    text = normalize_pasted_job_url(url)
    m = _LINKEDIN_VIEW_RE.search(text)
    if m:
        return m.group(1)
    m = _LINKEDIN_CURRENT_RE.search(text)
    if m:
        return m.group(1)
    return None


def extract_indeed_job_id(url: str) -> Optional[str]:
    jk = extract_indeed_jk_from_url(normalize_pasted_job_url(url) or "")
    return jk or None


def resolve_job_id(url: str, source: str | None = None) -> Optional[str]:
    """Extract the same job_id that scrapers persist for this URL/source."""
    url = normalize_pasted_job_url(url)
    source = source or detect_source(url)
    if source == "linkedin":
        return extract_linkedin_job_id(url)
    if source == "indeed":
        return extract_indeed_job_id(url)
    return job_id_from_url(url) if url else None


def canonical_job_link(url: str, source: str = "", job_id: str = "") -> str:
    """Stable URL stored on the Tracker card."""
    source = source or detect_source(url)
    job_id = job_id or (resolve_job_id(url, source) or "")
    if source == "linkedin" and job_id:
        return f"https://www.linkedin.com/jobs/view/{job_id}/"
    if source == "indeed" and job_id:
        return f"https://de.indeed.com/viewjob?jk={job_id}"
    return canonicalize_url(url) if url else ""


def link_only_job_title(url: str, source: str = "", job_id: str = "") -> str:
    source = source or detect_source(url)
    job_id = job_id or (resolve_job_id(url, source) or "")
    if source == "linkedin" and job_id:
        return f"(Link only) LinkedIn {job_id}"
    if source == "indeed" and job_id:
        return f"(Link only) Indeed {job_id}"
    short = (url or "").strip()
    if len(short) > 72:
        short = short[:69] + "…"
    return f"(Link only) {short or 'job'}"


def job_stub_from_url(url: str, scrape_warning: str = "") -> Optional[dict]:
    """
    Build a saveable job dict from the URL alone (no browser).

    Used when Chrome / Indeed / AI is unavailable so Log Job still records
    the application instead of silently dropping the paste.
    """
    url = normalize_pasted_job_url(url)
    if not url:
        return None
    source = detect_source(url)
    job_id = resolve_job_id(url, source)
    if not job_id:
        return None
    return {
        "title": link_only_job_title(url, source, job_id),
        "company": "",
        "location": "",
        "applicants": "",
        "description": "",
        "link": canonical_job_link(url, source, job_id),
        "job_id": job_id,
        "source": source,
        "scrape_warning": scrape_warning or "Details were not fetched",
    }


def plan_manual_log_action(on_tracker: bool, status: str, job_id: str) -> str:
    """
    Decide what Log Job should do for one URL.

    Returns:
        fail          — cannot resolve a job_id
        skip          — already on Tracker and not marking applied
        mark_applied  — already on Tracker; user asked for applied
        save          — insert / upsert a Tracker row
    """
    if not job_id:
        return "fail"
    if on_tracker:
        return "mark_applied" if status == "applied" else "skip"
    return "save"
