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
1. Opens the URL in the existing debug Chrome session (CDP).
2. Scrapes title / company / location / description.
   - LinkedIn / Indeed: site-specific selectors
   - Other URLs: trafilatura main-content extract + AI metadata
3. Runs AI matching (score stored but NOT used as a filter).
4. Saves the job to matched_jobs with status="pending" (default) or "applied".
5. Also upserts a record in the main jobs collection.
"""
import sys
import os
import re
import json
import hashlib
import argparse
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse, urlunparse

from dotenv import load_dotenv
load_dotenv()

# ── path setup ────────────────────────────────────────────────────────────────
_SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import trafilatura

from core.db_mongo import init_db, get_collection, mark_job_as_matched, save_job
from matching.ai_matcher import analyze_job_with_ai, load_user_profile, load_matching_criteria, _analysis_fields
from core.scraper_utils import (
    connect_browser,
    strip_html,
    pause,
    is_non_english_job_detail,
    safe_text,
    extract_indeed_jk_from_url,
    extract_indeed_detail_meta,
)
from core.config import INDEED_CONFIG

# Minimum chars of extracted body text before we treat a generic page as a JD
_GENERIC_MIN_DESC_CHARS = 200

# ── helpers ───────────────────────────────────────────────────────────────────

def detect_source(url: str) -> str:
    """Return 'linkedin', 'indeed', or 'manual' based on the URL hostname."""
    url_lower = url.lower()
    if "linkedin.com" in url_lower:
        return "linkedin"
    if "indeed.com" in url_lower:
        return "indeed"
    return "manual"


def canonicalize_url(url: str) -> str:
    """Strip fragment and trailing slash for stable job_id hashing."""
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", parsed.query, ""))


def job_id_from_url(url: str) -> str:
    """Stable short id derived from the canonical URL (for non-LinkedIn/Indeed)."""
    return hashlib.sha1(canonicalize_url(url).encode("utf-8")).hexdigest()[:16]


def extract_linkedin_job_id(url: str) -> Optional[str]:
    # /jobs/view/1234567890/
    m = re.search(r"/jobs/view/(\d+)", url)
    if m:
        return m.group(1)
    # ?currentJobId=1234567890  (search-results page with detail panel open)
    m = re.search(r"[?&]currentJobId=(\d+)", url)
    if m:
        return m.group(1)
    return None


def extract_indeed_job_id(url: str) -> Optional[str]:
    jk = extract_indeed_jk_from_url(url or "")
    return jk or None


# ── per-source scrapers ───────────────────────────────────────────────────────

def scrape_linkedin_job(page, url: str) -> Optional[dict]:
    """
    Navigate to a LinkedIn search-results page with currentJobId and extract
    job data using the same selectors as linkedin_scraper.py.
    """
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
    except PlaywrightTimeoutError:
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
                pause(0.5, 1.0)
                return
        except Exception:
            continue


def scrape_indeed_job(page, url: str) -> Optional[dict]:
    """
    Navigate to an Indeed viewjob URL and extract job data.
    Mirrors the extraction logic of indeed_scraper.py.
    """
    job_id = extract_indeed_job_id(url)
    if not job_id:
        print(f"  ⚠️  Cannot extract Indeed job_id from: {url}")
        return None

    # Canonical Indeed URL (strip tracking params, keep only jk=)
    canonical_url = f"https://de.indeed.com/viewjob?jk={job_id}"

    print(f"  → Navigating to {url}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        print(f"  ⚠️  Navigation error: {e}")
        return None

    # Dismiss cookie / consent dialogs — same as indeed_scraper.py
    _dismiss_indeed_overlays(page)

    try:
        page.wait_for_selector(
            f"{_INDEED_DETAIL_SELECTOR}, h1, #jobDescriptionText",
            timeout=15000,
        )
    except PlaywrightTimeoutError:
        print("  ⚠️  Timed out waiting for Indeed job detail; are you logged in?")
        return None

    pause(1.5, 2.5)

    # Prefer DOM header fields; AI inference remains a fallback in process_urls.
    company, location = extract_indeed_detail_meta(page)
    title = safe_text(page.locator('[data-testid="vj-job-title"]')) or ""

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
        "applicants": "",
        "description": description,
        "link": canonical_url,
        "job_id": job_id,
        "source": "indeed",
        "status": "applied",
    }


def _extract_main_text_from_html(html: str, url: str) -> str:
    """
    Pull the main article/job body from raw HTML via trafilatura.
    Returns empty string if extraction fails.
    """
    if not html:
        return ""
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
    pause(1.5, 2.5)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except PlaywrightTimeoutError:
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


# ── DB helpers ────────────────────────────────────────────────────────────────

def force_save_manual_job(
    job: dict,
    analysis: dict,
    status: str = "pending",
    applied_date: datetime = None,
) -> bool:
    """
    Save to matched_jobs regardless of AI score.
    If the job already exists in matched_jobs, update it instead of inserting.
    Also upsert the record in the main jobs collection.

    Args:
        job: Scraped job data dict
        analysis: AI analysis result dict
        status: Tracker status — "pending" (Not Applied, default) or "applied"
        applied_date: Date the user applied (only used when status=applied; defaults to now)
    """
    if status not in ("pending", "applied"):
        status = "pending"

    jobs_col = get_collection("jobs")
    matched_col = get_collection("matched_jobs")

    now = datetime.utcnow()
    # For applied jobs, timestamps use the user-provided applied date (defaults to now)
    t = (applied_date or now) if status == "applied" else now
    job_id = job["job_id"]
    source = job["source"]

    jobs_set = {
        "title":       job.get("title", ""),
        "company":     job.get("company", ""),
        "location":    job.get("location", ""),
        "description": job.get("description", ""),
        "applicants":  job.get("applicants", ""),
        "status":      status,
        "source":      source,
        "matched_at":  t,
        "updated_at":  t,
        "match_score": analysis.get("match_score", 0),
        **_analysis_fields(analysis),
    }
    if status == "applied":
        jobs_set["applied_at"] = t

    # ── 1. Upsert into main jobs collection ──────────────────────────────
    jobs_col.update_one(
        {"job_id": job_id, "source": source},
        {
            "$setOnInsert": {"created_at": t, "link": job.get("link", "")},
            "$set": jobs_set,
        },
        upsert=True,
    )

    # ── 2. Upsert into matched_jobs collection ───────────────────────────
    # NOTE: created_at must NOT appear in $set — it lives only in $setOnInsert
    # so that re-runs do not overwrite the original insert timestamp.
    # Putting the same key in both $set and $setOnInsert causes a MongoDB
    # WriteError ("conflict at 'created_at'") and silently aborts the write.
    matched_data = {
        "title":       job.get("title", ""),
        "company":     job.get("company", ""),
        "location":    job.get("location", ""),
        "link":        job.get("link", ""),
        "job_id":      job_id,
        "source":      source,
        "description": job.get("description", ""),
        "applicants":  job.get("applicants", ""),
        "match_score": analysis.get("match_score", 0),
        **_analysis_fields(analysis),
        "status":      status,
        "matched_at":  t,
        "updated_at":  t,
        # created_at is intentionally omitted here — set only on first insert below
        "notes":       "",
        "application_qa": [],
        "manually_logged": True,
    }
    if status == "applied":
        matched_data["applied_at"] = t
        matched_data["manually_applied"] = True

    try:
        matched_col.update_one(
            {"job_id": job_id, "source": source},
            {"$set": matched_data, "$setOnInsert": {"created_at": t}},
            upsert=True,
        )
    except Exception as e:
        print(f"  ❌ Failed to save to matched_jobs: {type(e).__name__}: {e}")
        return False
    return True


# Keep old name as alias for any external callers
force_save_applied_job = force_save_manual_job


# ── main processor ────────────────────────────────────────────────────────────

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

    user_profile = load_user_profile()
    if not user_profile:
        print("❌ No user profile found. Create docs/user_profile.md first.")
        return

    criteria = load_matching_criteria()

    results = {"ok": [], "failed": []}

    with sync_playwright() as playwright:
        browser = connect_browser(playwright, "job sites")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()

        for i, url in enumerate(urls, 1):
            url = url.strip()
            if not url:
                continue
            print(f"\n{'='*60}")
            print(f"[{i}/{len(urls)}] {url}")

            source = detect_source(url)
            try:
                if source == "linkedin":
                    job = scrape_linkedin_job(page, url)
                elif source == "indeed":
                    job = scrape_indeed_job(page, url)
                else:
                    print("  🌐 Generic career page — using trafilatura extract")
                    job = scrape_generic_job(page, url)
            except Exception as e:
                print(f"  ❌ Unexpected error while scraping: {type(e).__name__}: {e}")
                results["failed"].append(url)
                continue

            if not job:
                print(f"  ❌ Scraping failed")
                results["failed"].append(url)
                continue

            print(f"  📋 Title   : {job.get('title') or '(not found)'}")
            print(f"  🏢 Company : {job.get('company') or '(not found)'}")
            print(f"  📍 Location: {job.get('location') or '(not found)'}")
            print(f"  📝 Desc    : {len(job.get('description', ''))} chars")

            # Only description is mandatory — company/location/applicants can be empty
            if not job.get("description"):
                print("  ⚠️  Description is empty — page may not have loaded or login required. Skipping.")
                results["failed"].append(url)
                continue

            # For LinkedIn/Indeed the detail panel may not expose title/company/location
            # as separate DOM elements. Ask AI to infer any missing fields from the
            # plain-text description (already stripped of HTML/CSS).
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

            # Fill in a fallback title so the AI prompt always has something
            if not job.get("title"):
                job["title"] = f"(Unknown title — job_id {job.get('job_id', '')})"
                print(f"  ⚠️  Title not found, using fallback: {job['title']}")

            # Language check (English-only gate; non-EN still saved for manual log)
            should_skip, lang, de_share = is_non_english_job_detail(job.get("description", ""))
            if should_skip:
                print(
                    f"  ⚠️  Description is non-English ({lang}, de_share={de_share:.0%}) "
                    f"— saving anyway (manual job log)"
                )

            # AI matching
            print("  🤖 Running AI matching…")
            analysis = analyze_job_with_ai(job, user_profile, criteria)

            if not analysis:
                print("  ⚠️  AI analysis failed — saving with score=0")
                analysis = {
                    "match_score": 0,
                    "recommendation": "Unknown",
                    "summary": "AI analysis failed for this manually logged job.",
                }

            score = analysis.get("match_score", 0)
            print(f"  ✨ AI score: {score}/10  ({analysis.get('recommendation', '?')})")
            print(f"  💬 {analysis.get('summary', '')}")

            # Save regardless of score — status chosen by user (default: pending)
            force_save_manual_job(
                job, analysis, status=status, applied_date=applied_date
            )
            if status == "applied":
                date_str = applied_date.strftime("%Y-%m-%d") if applied_date else "today"
                print(f"  ✅ Saved to matched_jobs (status=applied, applied_at={date_str})")
            else:
                print("  ✅ Saved to matched_jobs (status=pending / Not Applied)")
            results["ok"].append(url)

            pause(2, 4, "  ⏳ Brief pause before next URL")

    print(f"\n{'='*60}")
    print(f"✅ Success : {len(results['ok'])}")
    print(f"❌ Failed  : {len(results['failed'])}")
    if results["failed"]:
        for u in results["failed"]:
            print(f"   • {u}")


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
    process_urls(urls, status=args.status)


if __name__ == "__main__":
    main()
