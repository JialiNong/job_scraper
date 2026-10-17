#!/usr/bin/env python3
"""
Manual Job Logger
Log job URLs into the tracker (LinkedIn / Indeed / any career page), whether or
not you have already applied.

Usage:
    # Pass URLs directly on the command line (default status: pending / Not Applied)
    python src/scrapers/manual_apply_scraper.py \
        "https://www.linkedin.com/jobs/view/1234567890/" \
        "https://de.indeed.com/viewjob?jk=abcdef123" \
        "https://careers.example.com/jobs/frontend-engineer"

    # Mark as already applied
    python src/scrapers/manual_apply_scraper.py --status applied "URL1" "URL2"

    # Or point to a text file with one URL per line
    python src/scrapers/manual_apply_scraper.py --file my_job_urls.txt

What it does for EACH url
--------------------------
1. Resolves job_id from the URL. Already on Tracker (matched_jobs): skip, or
   mark applied if --status applied. Rows only in jobs / timeout_jobs are
   still logged to Tracker.
2. Opens the URL in the existing debug Chrome session (CDP) when available.
3. Scrapes title / company / location / description.
   - LinkedIn / Indeed: site-specific selectors
   - Other URLs: trafilatura main-content extract + AI metadata
4. Runs AI matching when a description and user profile are available.
5. Saves the job to matched_jobs with status="pending" (default) or "applied".
   If Chrome / Indeed / AI fails, still saves the URL + parsed job_id
   (ai_pending stub) instead of dropping the paste.
6. Also upserts a record in the main jobs collection.

Link-only mode (--link-only / UI "Save Link Only")
--------------------------------------------------
Saves URL + job_id to matched_jobs with ai_pending=True and no description.
No Chrome / AI required. Later use Tracker "Run AI" to scrape + score.
"""
import sys
import os
import json
import argparse
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

# ── path setup ────────────────────────────────────────────────────────────────
_SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

from core.db_mongo import init_db, get_collection, is_job_on_tracker
from core.job_url import (
    canonicalize_url,
    detect_source,
    extract_indeed_job_id,
    extract_linkedin_job_id,
    job_id_from_url,
    job_stub_from_url,
    normalize_pasted_job_url,
    resolve_job_id,
)
from core.manual_log import (
    analysis_fields,
    force_save_manual_job,
    handle_existing_tracker,
    mark_tracker_job_applied,
    save_job_record,
    save_link_only_urls,
    stub_ai_pending_analysis,
)
from core.config import INDEED_CONFIG

# Aliases for callers that imported these names from this module
force_save_applied_job = force_save_manual_job


def _load_playwright():
    """Lazy import — Render's web image does not install Playwright."""
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    return sync_playwright, PlaywrightTimeoutError


def _su():
    """Lazy scraper_utils — not imported for Save Link Only."""
    import core.scraper_utils as su
    return su

# Minimum chars of extracted body text before we treat a generic page as a JD
_GENERIC_MIN_DESC_CHARS = 200

# ── per-source scrapers ───────────────────────────────────────────────────────

def scrape_linkedin_job(page, url: str) -> Optional[dict]:
    """
    Navigate to a LinkedIn search-results page with currentJobId and extract
    job data using the same selectors as linkedin_scraper.py.
    """
    su = _su()
    pause = su.pause
    safe_text = su.safe_text
    strip_html = su.strip_html

    job_id = extract_linkedin_job_id(url)
    if not job_id:
        print(f"  ⚠️  Cannot extract LinkedIn job_id from: {url}")
        return None

    # Canonical URL stored in DB (clean, no tracking params)
    canonical_url = f"https://www.linkedin.com/jobs/view/{job_id}/"

    # Navigate — use the original URL if it's already a search-results page,
    # otherwise build one so the left panel + right detail panel both load.
    if "search-results" in url:
        nav_url = url
    else:
        nav_url = (
            f"https://www.linkedin.com/jobs/search-results/"
            f"?currentJobId={job_id}"
            f"&keywords=frontend"
            f"&geoId=101282230"
            f"&origin=JOB_SEARCH_PAGE_JOB_FILTER"
            f"&f_TPR=r86400"
        )
    print(f"  → Navigating to {nav_url}")
    try:
        page.goto(nav_url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        print(f"  ⚠️  Navigation error: {e}")
        return None

    # Wait for the detail panel — new SDUI layout first, then classic fallbacks
    try:
        page.wait_for_selector(
            '[data-sdui-screen="com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"],'
            " .job-details-jobs-unified-top-card__tertiary-description-container,"
            " .jobs-box__html-content,"
            " [id*='JobDetails_AboutTheJob']",
            timeout=15000,
        )
    except Exception as e:
        if type(e).__name__ != "TimeoutError" and "Timeout" not in type(e).__name__:
            raise
        print("  ⚠️  Timed out waiting for LinkedIn job detail; are you logged in?")
        return None

    pause(1.0, 2.0)

    # ── Right panel only ──────────────────────────────────────────────────────
    # title/company/location are left empty — left panel cards may not be present
    title = ""
    company = ""
    location = ""

    applicants = safe_text(
        page.locator(".job-details-jobs-unified-top-card__tertiary-description-container")
    )

    # Description: new SDUI layout first (SemanticJobDetails > lazy-column),
    # then classic fallbacks. Use inner_text() for the SDUI path so we get clean
    # visible text directly; fall back to inner_html + strip_html for older layouts.
    description = ""

    # ── Primary: new LinkedIn SDUI layout ────────────────────────────────────
    try:
        screen_loc = page.locator(
            '[data-sdui-screen="com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"]'
        )
        if screen_loc.count() > 0:
            lazy_col = screen_loc.locator('[data-testid="lazy-column"]').first
            if lazy_col.count() > 0:
                desc_text = lazy_col.inner_text(timeout=8000) or ""
                desc_text = desc_text.strip()
                if desc_text:
                    description = desc_text
                    print('  🔍 Description found via: SemanticJobDetails > [data-testid="lazy-column"]')
    except Exception:
        pass

    # ── Fallbacks: classic / older LinkedIn layouts ───────────────────────────
    if not description:
        for sel in [
            f"#JobDetails_AboutTheJob_jobs_{job_id}",    # exact ID with job_id
            "[id*='JobDetails_AboutTheJob']",             # partial ID match
            ".jobs-box__html-content",                    # older layout
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    desc_html = loc.inner_html(timeout=5000) or ""
                    if desc_html:
                        description = strip_html(desc_html)
                        if description:
                            print(f"  🔍 Description found via: {sel}")
                            break
            except Exception:
                pass

    return {
        "title": title,
        "company": company,
        "location": location,
        "applicants": applicants,
        "description": description,
        "link": canonical_url,
        "job_id": job_id,
        "source": "linkedin",
        "status": "applied",
    }


_INDEED_DETAIL_SELECTOR = INDEED_CONFIG["selectors"]["detail"]  # '[data-testid="viewjob-main-content"]'


def _dismiss_indeed_overlays(page):
    """Best-effort dismiss cookie / consent dialogs on Indeed (mirrors indeed_scraper.py)."""
    candidates = [
        "button#onetrust-accept-btn-handler",
        'button:has-text("Alle akzeptieren")',
        'button:has-text("Accept All")',
        'button:has-text("Accept cookies")',
        'button:has-text("Ich stimme zu")',
    ]
    for selector in candidates:
        try:
            btn = page.locator(selector)
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click(timeout=2000)
                _su().pause(0.5, 1.0)
                return
        except Exception:
            continue


def scrape_indeed_job(page, url: str) -> Optional[dict]:
    """
    Navigate to an Indeed viewjob URL and extract job data.

    Always returns a job dict when jk= can be parsed — even if Chrome is
    blocked, the detail timed out, or Indeed showed a challenge. Callers
    must still save that stub instead of dropping the URL.
    """
    su = _su()
    from core.challenge_wait import is_indeed_challenge_page

    url = normalize_pasted_job_url(url)
    job_id = extract_indeed_job_id(url)
    if not job_id:
        print(f"  ⚠️  Cannot extract Indeed job_id from: {url}")
        return None

    # Canonical Indeed URL (strip tracking params, keep only jk=)
    canonical_url = f"https://de.indeed.com/viewjob?jk={job_id}"
    job = {
        "title": "",
        "company": "",
        "location": "",
        "applicants": "",
        "description": "",
        "link": canonical_url,
        "job_id": job_id,
        "source": "indeed",
        "scrape_warning": "",
    }

    print(f"  → Navigating to {canonical_url}")
    try:
        page.goto(canonical_url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        job["scrape_warning"] = f"Navigation error: {e}"
        print(f"  ⚠️  {job['scrape_warning']}")
        return job

    # Dismiss cookie / consent dialogs — same as indeed_scraper.py
    _dismiss_indeed_overlays(page)

    try:
        if is_indeed_challenge_page(page):
            job["scrape_warning"] = (
                "Indeed challenge/captcha page — details not fetched"
            )
            print(f"  ⚠️  {job['scrape_warning']}")
            return job
    except Exception:
        pass

    try:
        page.wait_for_selector(
            f"{_INDEED_DETAIL_SELECTOR}, h1, #jobDescriptionText",
            timeout=15000,
        )
    except Exception as e:
        if type(e).__name__ != "TimeoutError" and "Timeout" not in type(e).__name__:
            raise
        job["scrape_warning"] = (
            "Timed out waiting for Indeed job detail (login or block?)"
        )
        print(f"  ⚠️  {job['scrape_warning']}")

    su.pause(1.5, 2.5)

    # Prefer DOM header fields; AI inference remains a fallback in process_urls.
    try:
        company, location = su.extract_indeed_detail_meta(page)
    except Exception:
        company, location = "", ""
    title = ""
    try:
        title = su.safe_text(page.locator('[data-testid="vj-job-title"]')) or ""
    except Exception:
        pass

    # ── Description ───────────────────────────────────────────────────────
    # Prefer the JD body; fall back to the full detail container.
    description = ""
    for sel in [
        "#jobDescriptionText",
        ".jobsearch-JobComponent-description",
        ".simple-job-description-html",
        _INDEED_DETAIL_SELECTOR,
    ]:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                desc_html = loc.inner_html(timeout=8000) or ""
                if desc_html:
                    description = su.strip_html(desc_html)
                    if description:
                        print(f"  🔍 Description found via: {sel}")
                        break
        except Exception:
            pass

    job["title"] = title
    job["company"] = company
    job["location"] = location
    job["description"] = description
    if not description and not title and not job["scrape_warning"]:
        job["scrape_warning"] = "Indeed page loaded but title/description were empty"
    return job


def _extract_main_text_from_html(html: str, url: str) -> str:
    """
    Pull the main article/job body from raw HTML via trafilatura.
    Returns empty string if extraction fails.
    """
    if not html:
        return ""
    import trafilatura
    text = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=True,
        favor_precision=True,
        output_format="txt",
    )
    return (text or "").strip()


def _fallback_page_text(page) -> str:
    """Last-resort plain text from semantic containers or body."""
    for selector in ("main", "article", "[role='main']", "body"):
        try:
            loc = page.locator(selector).first
            if loc.count() == 0:
                continue
            text = (loc.inner_text(timeout=3000) or "").strip()
            if len(text) >= _GENERIC_MIN_DESC_CHARS:
                return text
        except Exception:
            continue
    return ""


def scrape_generic_job(page, url: str) -> Optional[dict]:
    """
    Open any career-page URL, extract main content with trafilatura,
    and return a job dict (title/company/location filled later by AI if missing).
    """
    canonical_url = canonicalize_url(url)
    job_id = job_id_from_url(canonical_url)

    print(f"  → Navigating to {url}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
    except Exception as e:
        print(f"  ⚠️  Navigation error: {e}")
        return None

    # Give SPA career sites a moment to hydrate
    _su().pause(1.5, 2.5)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass

    html = ""
    try:
        html = page.content()
    except Exception as e:
        print(f"  ⚠️  Could not read page HTML: {e}")

    description = _extract_main_text_from_html(html, canonical_url)
    if description:
        print(f"  📄 trafilatura extracted {len(description)} chars")
    else:
        print("  ⚠️  trafilatura returned empty — falling back to page text")
        description = _fallback_page_text(page)
        if description:
            print(f"  📄 fallback text: {len(description)} chars")

    if len(description) < _GENERIC_MIN_DESC_CHARS:
        print(
            f"  ⚠️  Extracted text too short ({len(description)} chars "
            f"< {_GENERIC_MIN_DESC_CHARS}) — not a usable job page"
        )
        return None

    # Leave title/company/location empty — process_urls will infer via AI
    try:
        hint = (page.title() or "").strip()
        if hint:
            print(f"  💡 Page <title> hint: {hint[:80]}")
    except Exception:
        pass

    return {
        "title": "",
        "company": "",
        "location": "",
        "applicants": "",
        "description": description,
        "link": canonical_url,
        "job_id": job_id,
        "source": "manual",
        "status": "applied",
    }


# ── AI metadata inference ─────────────────────────────────────────────────────

def infer_job_metadata_with_ai(description_text: str) -> dict:
    """
    Ask AI to infer job title, company, and location from plain-text description.

    The text passed in must already be stripped of HTML/CSS.
    Returns a dict with keys: title, company, location (any may be empty string).
    """
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("  ⚠️  No OPENAI_API_KEY found – cannot infer job metadata")
        return {}

    ai_model = os.getenv("AI_MODEL", "gpt-4.1-mini")

    # Truncate so the prompt stays cheap; 3 000 chars is plenty for header info
    trimmed = description_text[:3000]

    prompt = (
        "The following text was extracted from a job posting page "
        "(LinkedIn, Indeed, or a company careers site). "
        "Based only on the text below, infer the job title, company name, and location. "
        "If you cannot determine a field with reasonable confidence, return an empty string.\n\n"
        f"Job description text:\n{trimmed}\n\n"
        "Respond ONLY with valid JSON:\n"
        '{"title": "<job title>", "company": "<company name>", "location": "<location>"}'
    )

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=ai_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You extract job metadata (title, company, location) from job-posting text. "
                        "Respond only with valid JSON containing exactly three keys: "
                        "title, company, location."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)
        return result
    except Exception as e:
        print(f"  ⚠️  AI metadata inference failed: {e}")
        return {}


def apply_analysis_to_existing(
    job: dict,
    analysis: dict,
) -> bool:
    """
    Patch scraped fields + AI analysis onto existing jobs / matched_jobs rows.

    Preserves Tracker status, notes, timeline, highlights, application_qa,
    applied_at, and matched_at. Clears ai_pending.
    """
    jobs_col = get_collection("jobs")
    matched_col = get_collection("matched_jobs")
    now = datetime.utcnow()
    job_id = job["job_id"]
    source = job["source"]
    match_score = analysis.get("match_score", 0)

    patch = {
        "title":       job.get("title", ""),
        "company":     job.get("company", ""),
        "location":    job.get("location", ""),
        "description": job.get("description", ""),
        "applicants":  job.get("applicants", ""),
        "link":        job.get("link", ""),
        "match_score": match_score,
        **analysis_fields(analysis),
        "updated_at":  now,
        "ai_pending":  False,
    }

    try:
        jobs_col.update_one(
            {"job_id": job_id, "source": source},
            {
                "$set": {**patch, "matched_at": now},
                "$setOnInsert": {
                    "created_at": now,
                    "status": "new",
                    "source": source,
                },
            },
            upsert=True,
        )
        matched_col.update_one(
            {"job_id": job_id, "source": source},
            {"$set": patch},
        )
    except Exception as e:
        print(f"  ❌ Failed to update analysis: {type(e).__name__}: {e}")
        return False
    return True


# ── scrape + evaluate helpers ─────────────────────────────────────────────────

def scrape_job_page(page, url: str) -> Optional[dict]:
    """Scrape one URL with the already-open CDP page. Returns job dict or None."""
    source = detect_source(url)
    try:
        if source == "linkedin":
            return scrape_linkedin_job(page, url)
        if source == "indeed":
            return scrape_indeed_job(page, url)
        print("  🌐 Generic career page — using trafilatura extract")
        return scrape_generic_job(page, url)
    except Exception as e:
        print(f"  ❌ Unexpected error while scraping: {type(e).__name__}: {e}")
        return None


def enrich_job_and_evaluate(
    job: dict,
    user_profile: str,
    criteria: str,
    *,
    allow_non_english: bool = True,
) -> Optional[dict]:
    """
    Infer missing title/company/location, then run evaluate_job.
    Returns analysis dict (never None — failed AI becomes score=0 stub).
    Returns None only when description is empty (caller should abort).
    """
    print(f"  📋 Title   : {job.get('title') or '(not found)'}")
    print(f"  🏢 Company : {job.get('company') or '(not found)'}")
    print(f"  📍 Location: {job.get('location') or '(not found)'}")
    print(f"  📝 Desc    : {len(job.get('description', ''))} chars")

    if not job.get("description"):
        print("  ⚠️  Description is empty — page may not have loaded or login required.")
        return None

    if not (job.get("title") and job.get("company") and job.get("location")):
        missing = [f for f in ("title", "company", "location") if not job.get(f)]
        print(f"  🤖 Inferring missing field(s) {missing} from description via AI…")
        metadata = infer_job_metadata_with_ai(job.get("description", ""))
        if metadata:
            for field in ("title", "company", "location"):
                if not job.get(field):
                    job[field] = metadata.get(field, "")
            print(
                f"  🤖 Inferred → title='{job.get('title')}'"
                f"  company='{job.get('company')}'"
                f"  location='{job.get('location')}'"
            )

    if not job.get("title"):
        job["title"] = f"(Unknown title — job_id {job.get('job_id', '')})"
        print(f"  ⚠️  Title not found, using fallback: {job['title']}")

    from matching.ai_matcher import evaluate_job
    should_skip, lang, de_share = _su().is_non_english_job_detail(job.get("description", ""))
    if should_skip:
        if allow_non_english:
            print(
                f"  ⚠️  Description is non-English ({lang}, de_share={de_share:.0%}) "
                f"— continuing anyway (manual job log)"
            )
        else:
            print(
                f"  ⚠️  Description is non-English ({lang}, de_share={de_share:.0%})"
            )

    print("  🤖 Running AI matching…")
    analysis = evaluate_job(job, user_profile, criteria)
    if not analysis:
        print("  ⚠️  AI analysis failed — using score=0 stub")
        analysis = {
            "match_score": 0,
            "recommendation": "Unknown",
            "summary": "AI analysis failed for this manually logged job.",
        }

    score = analysis.get("match_score", 0)
    print(f"  ✨ AI score: {score}/10  ({analysis.get('recommendation', '?')})")
    print(f"  💬 {analysis.get('summary', '')}")
    return analysis


# ── main processors ───────────────────────────────────────────────────────────

def process_urls(
    urls: list[str],
    applied_date: datetime = None,
    status: str = "pending",
):
    """
    Scrape, AI-match, and save each URL to the tracker.

    Args:
        urls: LinkedIn / Indeed / any company career-page job URLs
        applied_date: Date the user applied (only used when status=applied)
        status: "pending" (Not Applied, default) or "applied"
    """
    if status not in ("pending", "applied"):
        status = "pending"

    if not urls:
        print("No URLs provided.")
        return

    status_label = "applied" if status == "applied" else "pending (Not Applied)"
    print(f"\n🔗 Processing {len(urls)} URL(s) as {status_label}…")

    try:
        sync_playwright, _ = _load_playwright()
        from core.scraper_utils import connect_browser, pause
        from matching.ai_matcher import load_user_profile, load_matching_criteria
    except ImportError as e:
        print(f"⚠️  Scrape is unavailable ({e}). Saving link(s) only.")
        save_link_only_urls(urls, applied_date=applied_date, status=status)
        return

    user_profile = load_user_profile()
    if not user_profile:
        print(
            "⚠️  No user profile found (docs/user_profile.md). "
            "Will save links without AI scoring."
        )
    criteria = load_matching_criteria()
    stub_analysis = stub_ai_pending_analysis()

    results = {"ok": [], "failed": [], "skipped": []}
    page = None

    try:
        with sync_playwright() as playwright:
            try:
                browser = connect_browser(playwright, "job sites")
                context = browser.contexts[0]
                page = context.pages[0] if context.pages else context.new_page()
            except Exception as e:
                print(
                    f"❌ Cannot open Chrome ({e}). "
                    "Saving URL(s) without scrape."
                )
                page = None

            for i, raw in enumerate(urls, 1):
                url = normalize_pasted_job_url(raw)
                if not url:
                    continue
                print(f"\n{'='*60}")
                print(f"[{i}/{len(urls)}] {url}")

                source = detect_source(url)
                job_id = resolve_job_id(url, source)
                if not job_id:
                    print("  ❌ Cannot resolve job_id from URL")
                    results["failed"].append(url)
                    continue
                if is_job_on_tracker(job_id, source):
                    outcome = handle_existing_tracker(
                        url, job_id, source, status, applied_date
                    )
                    if outcome == "applied":
                        results["ok"].append(url)
                    elif outcome in ("already", "skip"):
                        results["skipped"].append(url)
                    if outcome != "missing":
                        continue

                job = None
                if page is not None:
                    job = scrape_job_page(page, url)
                if not job:
                    warn = (
                        "Scraping failed"
                        if page is not None
                        else "Chrome was not available"
                    )
                    job = job_stub_from_url(url, scrape_warning=warn)
                if not job:
                    print("  ❌ Cannot build a job record from this URL")
                    results["failed"].append(url)
                    continue

                warning = (job.get("scrape_warning") or "").strip()
                analysis = None
                ai_pending = False
                if job.get("description") and user_profile:
                    analysis = enrich_job_and_evaluate(job, user_profile, criteria)
                if not analysis:
                    analysis = stub_analysis
                    ai_pending = True
                    if warning:
                        print(
                            f"  ⚠️  {warning} — saving with URL and parsed fields "
                            "(AI pending)"
                        )
                    elif not job.get("description"):
                        print("  ⚠️  No description — saving link only (AI pending)")
                    else:
                        print("  ⚠️  No user profile — saving without AI (AI pending)")

                extra = "AI pending" if ai_pending else ""
                if not save_job_record(
                    job, analysis, status, applied_date,
                    ai_pending=ai_pending, extra=extra,
                ):
                    results["failed"].append(url)
                    continue
                results["ok"].append(url)

                if page is not None:
                    pause(2, 4, "  ⏳ Brief pause before next URL")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        remaining = [u for u in urls if u not in results["ok"] and u not in results["failed"] and u not in results["skipped"]]
        if remaining:
            print("  → Falling back to link-only save for remaining URLs")
            save_link_only_urls(remaining, applied_date=applied_date, status=status)
            return

    print(f"\n{'='*60}")
    print(f"✅ Success : {len(results['ok'])}")
    print(f"⏭️  Skipped : {len(results['skipped'])} (already on Tracker)")
    print(f"❌ Failed  : {len(results['failed'])}")
    if results["skipped"]:
        for u in results["skipped"]:
            print(f"   • {u}")
    if results["failed"]:
        for u in results["failed"]:
            print(f"   • {u}")


def run_ai_on_matched_job(matched_doc: dict) -> dict:
    """
    Open the job link in debug Chrome, scrape JD, run AI matching, and patch
    the existing matched_jobs (+ jobs) row without wiping notes/timeline.

    Returns {"ok": bool, "error": str|None, "match_score": ...}.
    """
    url = (matched_doc.get("link") or "").strip()
    if not url:
        job_id = matched_doc.get("job_id") or ""
        source = matched_doc.get("source") or ""
        if source == "linkedin" and job_id:
            url = f"https://www.linkedin.com/jobs/view/{job_id}/"
        elif source == "indeed" and job_id:
            url = f"https://de.indeed.com/viewjob?jk={job_id}"
    if not url:
        print("  ❌ No link on this Tracker card")
        return {"ok": False, "error": "No job link", "match_score": None}

    print(f"\n🤖 Run AI for Tracker job: {matched_doc.get('title') or matched_doc.get('job_id')}")
    print(f"  → {url}")

    try:
        sync_playwright, _ = _load_playwright()
        from core.scraper_utils import connect_browser
        from matching.ai_matcher import load_user_profile, load_matching_criteria
    except ImportError as e:
        print(f"  ❌ Scrape is unavailable ({e})")
        return {"ok": False, "error": f"Scrape unavailable: {e}", "match_score": None}

    user_profile = load_user_profile()
    if not user_profile:
        print("❌ No user profile found. Create docs/user_profile.md first.")
        return {"ok": False, "error": "Missing user profile", "match_score": None}

    criteria = load_matching_criteria()

    with sync_playwright() as playwright:
        browser = connect_browser(playwright, "job sites")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()

        job = scrape_job_page(page, url)
        if not job:
            print("  ❌ Scraping failed")
            return {"ok": False, "error": "Scraping failed", "match_score": None}

        # Prefer existing user-edited title/company/location if scrape left them empty
        for field in ("title", "company", "location"):
            if not job.get(field) and matched_doc.get(field):
                existing = str(matched_doc.get(field) or "")
                if existing and not existing.startswith("(Link only)"):
                    job[field] = existing

        analysis = enrich_job_and_evaluate(job, user_profile, criteria)
        if not analysis:
            print("  ❌ No description — cannot run AI")
            return {"ok": False, "error": "Empty description after scrape", "match_score": None}

        ok = apply_analysis_to_existing(job, analysis)
        if not ok:
            return {"ok": False, "error": "DB update failed", "match_score": None}

        print("  ✅ AI analysis saved to Tracker")
        return {
            "ok": True,
            "error": None,
            "match_score": analysis.get("match_score"),
            "title": job.get("title", ""),
            "company": job.get("company", ""),
        }


def main():
    parser = argparse.ArgumentParser(
        description="Scrape job URLs, AI-match, and save to the tracker "
                    "(default status: pending / Not Applied)."
    )
    parser.add_argument(
        "urls",
        nargs="*",
        metavar="URL",
        help="One or more job URLs (LinkedIn, Indeed, or any company careers page)",
    )
    parser.add_argument(
        "--file",
        "-f",
        metavar="FILE",
        help="Text file with one URL per line",
    )
    parser.add_argument(
        "--status",
        choices=("pending", "applied"),
        default="pending",
        help="Tracker status after save (default: pending / Not Applied)",
    )
    parser.add_argument(
        "--link-only",
        action="store_true",
        help="Save URLs to Tracker without scraping or AI (Run AI later from UI)",
    )
    args = parser.parse_args()

    urls: list[str] = list(args.urls)
    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as fh:
                file_urls = [line.strip() for line in fh if line.strip() and not line.startswith("#")]
            urls.extend(file_urls)
            print(f"📂 Loaded {len(file_urls)} URL(s) from {args.file}")
        except FileNotFoundError:
            print(f"❌ File not found: {args.file}")
            sys.exit(1)

    if not urls:
        parser.print_help()
        sys.exit(0)

    init_db()
    if args.link_only:
        save_link_only_urls(urls, status=args.status)
    else:
        process_urls(urls, status=args.status)


if __name__ == "__main__":
    main()
