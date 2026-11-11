"""
Parse and normalise job URLs pasted into Log Job / the manual logger.

Kept free of Playwright so URL handling can be unit-tested without Chrome.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse

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


# Indeed short-link hosts (the Indeed connector / share buttons hand these out).
# They 302 to /uie/clk?…&jk=<key>, so one hop is enough to get the job key.
_SHORT_LINK_HOSTS = {"to.indeed.com"}
_SHORT_LINK_MAX_HOPS = 4
_SHORT_LINK_TIMEOUT = 8


def is_short_job_url(url: str) -> bool:
    host = (urlparse(normalize_pasted_job_url(url)).hostname or "").lower()
    return host in _SHORT_LINK_HOSTS


def expand_short_job_url(url: str) -> str:
    """
    Follow an Indeed short link (to.indeed.com/…) until a URL with a job key
    appears, and return that URL. Any other URL is returned normalised and
    unchanged; on network errors the short URL is returned as-is.

    Redirects are followed by hand and stop as soon as jk= shows up, so the
    real job page (Cloudflare-protected) is never requested.
    """
    import urllib.error
    import urllib.request

    url = normalize_pasted_job_url(url)
    if not url or not is_short_job_url(url):
        return url

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    current = url
    for _ in range(_SHORT_LINK_MAX_HOPS):
        req = urllib.request.Request(
            current, method="GET", headers={"User-Agent": "Mozilla/5.0"}
        )
        try:
            resp = opener.open(req, timeout=_SHORT_LINK_TIMEOUT)
            resp.close()
            break  # 2xx with no redirect: nothing more to follow
        except urllib.error.HTTPError as e:
            location = e.headers.get("Location") if e.code in (301, 302, 303, 307, 308) else None
            e.close()
            if not location:
                break
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"  ⚠️  Could not expand short link {url}: {e}")
            break
        current = urljoin(current, location)
        if extract_indeed_jk_from_url(current):
            return current
    return url


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


# ---------- posting key: one id per job ad, whatever the URL decoration ----------
#
# job_id for non-LinkedIn/Indeed links hashes the whole URL, so
# `…/job/2811207` and `…/job/2811207?language=en` got two ids, and a link
# logged by hand never matched the same ad found by the ATS source
# (job_id "personio:bookboost:2811207"). The posting key is parsed from the
# link itself and stored on every row, so dedup can match across sources.

# ATS hosts whose URL carries the posting id. Ids that are globally unique on
# that ATS (Greenhouse, Lever, Ashby, Workable, SmartRecruiters, JOIN) skip the
# company slug so a company careers page (?gh_jid=…) matches the board URL.
_ATS_KEY_PATTERNS = [
    ("greenhouse", re.compile(r"(?:^|\.)greenhouse\.io$"), re.compile(r"/jobs/(\d+)"), False),
    ("lever", re.compile(r"^jobs\.(?:eu\.)?lever\.co$"), re.compile(r"^/[^/]+/([0-9a-f-]{36})"), False),
    ("ashby", re.compile(r"^jobs\.ashbyhq\.com$"), re.compile(r"^/[^/]+/([0-9a-f-]{36})"), False),
    ("personio", re.compile(r"^([^.]+)\.jobs\.personio\.(?:de|com)$"), re.compile(r"^/job/(\d+)"), True),
    ("workable", re.compile(r"^apply\.workable\.com$"), re.compile(r"^/[^/]+/j/([0-9A-Za-z]+)"), False),
    ("workable", re.compile(r"^[^.]+\.workable\.com$"), re.compile(r"^/j/([0-9A-Za-z]+)"), False),
    ("recruitee", re.compile(r"^([^.]+)\.recruitee\.com$"), re.compile(r"^/o/([^/]+)"), True),
    ("smartrecruiters", re.compile(r"^(?:jobs|careers)\.smartrecruiters\.com$"), re.compile(r"^/[^/]+/(\d+)"), False),
    ("join", re.compile(r"^join\.com$"), re.compile(r"^/companies/[^/]+/(\d+)"), False),
    ("teamtailor", re.compile(r"^([^.]+)\.teamtailor\.com$"), re.compile(r"/jobs/(\d+)"), True),
    ("bamboohr", re.compile(r"^([^.]+)\.bamboohr\.com$"), re.compile(r"/careers/(\d+)"), True),
    ("workday", re.compile(r"^([^.]+)\.wd\d+\.myworkdayjobs\.com$"), re.compile(r"/job/.*_([A-Za-z0-9-]+)$"), True),
]
# Embedded-board ids on company careers pages
_ATS_QUERY_KEYS = {"gh_jid": "greenhouse", "ashby_jid": "ashby"}

# Query params that never identify the job: language, tracking, referrers
_NOISE_PARAMS = {
    "language", "lang", "locale", "hl", "em", "ref", "referrer", "referer",
    "source", "src", "trk", "trackingid", "gh_src", "lever-source", "lever-origin",
    "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "_hsenc", "_hsmi", "mode",
    "from", "via", "channel", "medium", "campaign",
}


def posting_key(url: str) -> Optional[str]:
    """
    Stable id of the job ad behind a URL, or None for LinkedIn / Indeed
    (their job_id already is one).

    Known ATS boards → "personio:bookboost:2811207", "greenhouse:4012345" …
    Any other site → "url:host/path?kept=params" with language / tracking
    params dropped, so `?language=en` or `?utm_source=…` copies still match.
    """
    from urllib.parse import parse_qsl, urlencode

    text = normalize_pasted_job_url(url)
    if not text:
        return None
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host or "linkedin.com" in host or "indeed.com" in host:
        return None
    path = parsed.path.rstrip("/")
    query = parse_qsl(parsed.query, keep_blank_values=False)

    for param, value in query:
        provider = _ATS_QUERY_KEYS.get(param.lower())
        if provider and value.strip():
            return f"{provider}:{value.strip().lower()}"

    for provider, host_re, path_re, with_slug in _ATS_KEY_PATTERNS:
        hm = host_re.search(host)
        if not hm:
            continue
        pm = path_re.search(path)
        if not pm:
            continue
        job = pm.group(1).lower()
        if not with_slug:
            return f"{provider}:{job}"
        slug = (hm.group(1) if hm.groups() else "").lower()
        return f"{provider}:{slug}:{job}"

    kept = sorted(
        (k, v) for k, v in query
        if k.lower() not in _NOISE_PARAMS and not k.lower().startswith("utm_")
    )
    return f"url:{host}{path or '/'}" + (f"?{urlencode(kept)}" if kept else "")


def posting_key_fields(url: str) -> dict:
    """{"posting_key": …} for a row's link, or {} when there is none."""
    key = posting_key(url or "")
    return {"posting_key": key} if key else {}


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


def replacement_link_fields(raw_url: str) -> tuple[dict | None, str | None]:
    """
    Parse a pasted replacement URL into Tracker identity fields.

    Returns ({link, source, job_id, posting_key?}, None) or (None, error).
    Expand Indeed short links the same way Log Job does. Does not write Mongo.
    """
    url = expand_short_job_url(normalize_pasted_job_url(raw_url))
    if not url:
        return None, "Paste a job URL"
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip()
    if parsed.scheme not in ("http", "https") or not host:
        return None, "Paste a full job URL (https://…)"
    source = detect_source(url)
    job_id = resolve_job_id(url, source)
    if not job_id:
        return None, "Could not parse a job id from this URL"
    link = canonical_job_link(url, source, job_id)
    fields = {"link": link, "source": source, "job_id": job_id}
    fields.update(posting_key_fields(link))
    return fields, None


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
