"""
Shared funnel for API job sources: listing → filters → `jobs` collection.

Same gates as the browser scrapers (title blacklist, auto-pass / AI title
check, dedup, JD completeness, English-only), plus two API-only steps:

  * location gate — feeds like Arbeitnow and company ATS boards include
    Swiss / Dutch / US roles; keep Germany + remote (core.db_mongo.is_germany_or_remote)
  * cross-source dedup — skip roles already saved from another board
    (core.dedup.find_cross_source_duplicate)

Order is cheapest first; the full JD is fetched (and the AI title check paid
for) only after the free filters pass.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable, Optional

from core.config import API_TITLE_EXCLUDE_EXTRA, FEED_TITLE_INCLUDE_PATTERNS
from core.dedup import dedup_key, find_cross_source_duplicate
from core.scraper_utils import (
    description_save_flags,
    is_non_english_job_detail,
    is_title_excluded,
    is_title_relevant_by_ai,
    strip_html,
    title_matches_auto_pass,
)

_FEED_TITLE_RE = re.compile("|".join(FEED_TITLE_INCLUDE_PATTERNS), re.IGNORECASE)
_API_EXCLUDE_RE = re.compile("|".join(API_TITLE_EXCLUDE_EXTRA), re.IGNORECASE)

# Strict location check for global boards (company ATS): the posting must
# positively name Germany / a German city, or be remote without a foreign
# scope. core.db_mongo.is_germany_or_remote treats unknown places as German
# towns, which is right for the applied-jobs chart but lets "Austin, Texas"
# through on a US company's board.
_DE_RE = re.compile(r"(?<![a-z])(germany|deutschland)(?![a-z])")
_EXTRA_DE_CITIES = (
    "aachen", "darmstadt", "münster", "muenster", "bochum", "regensburg", "erlangen",
    "jena", "halle", "magdeburg", "kiel", "lübeck", "luebeck", "rostock", "braunschweig",
    "würzburg", "wuerzburg", "heilbronn", "ingolstadt", "chemnitz", "erfurt", "saarbrücken",
    "osnabrück", "bielefeld", "paderborn", "göttingen", "oldenburg", "ludwigsburg",
    "walldorf", "garching", "unterföhring", "eschborn", "gilching", "konstanz",
)
_EXTRA_DE_RE = re.compile(
    r"(?<![a-zäöüß])(" + "|".join(re.escape(c) for c in _EXTRA_DE_CITIES) + r")(?![a-zäöüß])"
)
_REMOTE_RE = re.compile(r"\b(remote|home\s?office|work from home|wfh)\b")
_REMOTE_SCOPE_OK_RE = re.compile(r"\b(europe|european|emea|eu|dach|cet|worldwide|anywhere|global)\b")
_LOCATION_FILLER_RE = re.compile(r"\b(remote|home\s?office|work from home|wfh|first|only|friendly|possible|job|hybrid)\b|[^a-z]+")


def is_germany_or_remote_strict(location: str) -> bool:
    from core.db_mongo import _city_in_text

    text = (location or "").lower()
    if not text.strip():
        return True
    if _DE_RE.search(text) or _city_in_text(text) or _EXTRA_DE_RE.search(text):
        return True
    if _REMOTE_RE.search(text):
        if _REMOTE_SCOPE_OK_RE.search(text):
            return True
        return not _LOCATION_FILLER_RE.sub("", text).strip()  # plain "Remote"
    return False


@dataclass
class SourceJob:
    """One listing from an API source, normalised to the `jobs` schema."""

    source: str  # jobs.source: arbeitnow / arbeitsagentur / adzuna / ats
    job_id: str
    title: str
    company: str
    location: str
    link: str
    description: str = ""  # HTML or plain text; empty → fetch_description()
    posted_at: Optional[datetime] = None
    remote: bool = False
    extra: dict = field(default_factory=dict)  # stored on the jobs row as-is
    fetch_description: Optional[Callable[[], str]] = None
    # "strict" (global boards), "lenient" (DE-focused feeds), "none" (source is DE-only)
    location_policy: str = "lenient"


def is_on_topic_title(title: str) -> bool:
    """Broad dev-role check for keyword-less feeds (Arbeitnow, ATS boards)."""
    return bool(_FEED_TITLE_RE.search(title or ""))


def is_api_title_excluded(title: str) -> bool:
    """Shared blacklist plus API-only extras (VP / Director / new grad …)."""
    return is_title_excluded(title) or bool(_API_EXCLUDE_RE.search(title or ""))


def location_ok(job: SourceJob) -> bool:
    if job.location_policy == "none" or not job.location:
        return True
    if job.location_policy == "strict":
        return is_germany_or_remote_strict(job.location)
    # Imported lazily so the funnel stays importable in unit tests
    from core.db_mongo import is_germany_or_remote

    return is_germany_or_remote(job.location)


def _plain(text: str) -> str:
    if not text:
        return ""
    if "<" in text and ">" in text:
        return strip_html(text)
    return text.strip()


def ingest(
    jobs: Iterable[SourceJob],
    *,
    require_on_topic_title: bool = False,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> Counter:
    """
    Run each listing through the funnel and save survivors to `jobs`.

    Returns a Counter of outcomes (saved, known, title_excluded, …).
    With dry_run nothing is written (no rows, no scraper_stats increments).
    """
    from core import db_mongo

    db = db_mongo.get_db()
    stats: Counter = Counter()
    seen: set[tuple[str, str]] = set()
    seen_keys: set[str] = set()  # company + title: one role posted per city

    def stat(key: str):
        if not dry_run:
            db_mongo.increment_scraper_stat(key)

    for job in jobs:
        stats["listed"] += 1
        label = f"[{job.source}] {job.title[:70]} | {job.company[:40]}"
        if not job.title or not job.link or not job.job_id:
            stats["invalid"] += 1
            continue
        key = (job.source, job.job_id)
        if key in seen:
            stats["repeat_in_run"] += 1
            continue
        seen.add(key)

        if not location_ok(job):
            stats["not_germany"] += 1
            continue
        if is_api_title_excluded(job.title):
            stats["title_excluded"] += 1
            continue
        if require_on_topic_title and not is_on_topic_title(job.title):
            stats["off_topic"] += 1
            continue
        if db_mongo.is_job_known(job.job_id, job.source):
            stats["known"] += 1
            continue
        key_ct = dedup_key(job.title, job.company)
        if key_ct in seen_keys:
            stats["repeat_in_run"] += 1
            continue
        seen_keys.add(key_ct)
        if db.jobs.find_one({"dedup_key": key_ct}, {"_id": 1}):
            stats["known"] += 1  # same company + title saved earlier (another city / reposted)
            continue
        dup = find_cross_source_duplicate(db, job.title, job.company, job.source)
        if dup:
            stats["cross_source_duplicate"] += 1
            log(f"  = {label} — already saved from {dup['source']}")
            continue
        if not title_matches_auto_pass(job.title) and not is_title_relevant_by_ai(job.title):
            stats["ai_title_filtered"] += 1
            stat("ai_title_filtered")
            log(f"  ✗ {label} — AI title check: unrelated")
            continue

        description = _plain(job.description)
        if not description and job.fetch_description:
            try:
                description = _plain(job.fetch_description() or "")
            except Exception as exc:  # detail call failed: save as empty JD
                log(f"  ⚠ {label} — description fetch failed: {exc}")
                description = ""
        stat("title_passed_clicked")

        flags = description_save_flags(
            description, title=job.title, company=job.company, source=job.source
        )
        if not (flags["description_empty"] or flags["description_incomplete"]):
            skip, lang, _share = is_non_english_job_detail(description)
            if skip:
                stats["german_filtered"] += 1
                stat("german_filtered")
                log(f"  ✗ {label} — JD language {lang}")
                continue

        doc = {
            "title": job.title.strip(),
            "company": job.company.strip(),
            "location": job.location.strip(),
            "link": job.link,
            "job_id": job.job_id,
            "source": job.source,
            "status": "new",
            "applicants": "",
            "description": description,
            "remote": bool(job.remote),
            "dedup_key": key_ct,
            **({"posted_at": job.posted_at} if job.posted_at else {}),
            **job.extra,
            **flags,
        }
        if dry_run:
            stats["would_save"] += 1
            log(f"  ✓ {label} — would save ({flags['description_quality']} JD, {len(description)} chars)")
            continue
        if db_mongo.save_job(doc):
            stats["saved"] += 1
        else:
            stats["save_failed_or_duplicate"] += 1
    return stats
