#!/usr/bin/env python3
"""
LinkedIn Job Scraper
Scrapes job listings from LinkedIn and saves them to MongoDB
"""
import sys
import os
from urllib.parse import quote_plus
import random
import re

# Add src/ to path so package imports work when run as a script
_SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from core.db_mongo import (
    init_db,
    save_job,
    is_job_id_exists,
    increment_scraper_stat,
    save_timeout_job,
)
from core.config import (
    LINKEDIN_JOBS_PER_PAGE,
    MAX_JOBS_PER_PAGE,
    LINKEDIN_CONFIG,
)
from core.scraper_utils import (
    pause,
    safe_text,
    strip_html,
    parse_args,
    connect_browser,
    open_scraper_page,
    goto_page,
    should_skip_title_before_click,
    is_non_english_job_detail,
    is_usable_job_description,
    DESCRIPTION_FETCH_RETRIES,
)

# LinkedIn configuration
BASE_URL = LINKEDIN_CONFIG["base_url"]
GEO_ID = LINKEDIN_CONFIG["geo_id"]
TIME_FILTER = LINKEDIN_CONFIG["time_filter"]
SOURCE = LINKEDIN_CONFIG["source"]


def hours_to_f_tpr(hours: float) -> str:
    """Map a posted-within window (hours) to LinkedIn's f_TPR value."""
    if hours is None or hours <= 0:
        raise ValueError(f"hours must be a positive number, got {hours!r}")
    seconds = int(round(float(hours) * 3600))
    if seconds <= 0:
        raise ValueError(f"hours too small after conversion: {hours!r}")
    return f"r{seconds}"


def resolve_time_filter(hours=None) -> str:
    """CLI --hours overrides config; otherwise use LINKEDIN_CONFIG time_filter."""
    if hours is None:
        return TIME_FILTER
    return hours_to_f_tpr(hours)

# LinkedIn selectors
JOB_CARD_SELECTOR = LINKEDIN_CONFIG["selectors"]["job_card"]
JOB_LINK_SELECTOR = LINKEDIN_CONFIG["selectors"]["job_link"]

# Logged-in /jobs/search-results/ (SDUI). Classes are obfuscated; the
# component key is the stable hook and includes the job id.
SDUI_JOB_CARD_SELECTOR = '[componentkey^="job-card-component-ref-"][role="button"]'
SDUI_NEXT_PAGE_SELECTOR = '[data-testid="pagination-controls-next-button-visible"]'

# Detail panel selectors — SDUI first, then classic LinkedIn layouts.
_LINKEDIN_DETAIL_WAIT = (
    '[data-sdui-screen="com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"],'
    " .job-details-jobs-unified-top-card__tertiary-description-container,"
    " .jobs-box__html-content,"
    " [id*='JobDetails_AboutTheJob']"
)


def extract_linkedin_description(page, job_id: str = "") -> str:
    """
    Pull JD text from the LinkedIn detail panel.

    Tries the newer SemanticJobDetails SDUI layout first, then classic
    About-the-job / jobs-box selectors. Returns "" if nothing usable is found.
    """
    # Primary: new LinkedIn SDUI layout
    try:
        screen_loc = page.locator(
            '[data-sdui-screen="com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"]'
        )
        if screen_loc.count() > 0:
            lazy_col = screen_loc.locator('[data-testid="lazy-column"]').first
            if lazy_col.count() > 0:
                desc_text = (lazy_col.inner_text(timeout=8000) or "").strip()
                if desc_text:
                    return desc_text
    except Exception:
        pass

    selectors = []
    if job_id:
        selectors.append(f"#JobDetails_AboutTheJob_jobs_{job_id}")
    selectors.extend(
        [
            "[id*='JobDetails_AboutTheJob']",
            ".jobs-box__html-content",
        ]
    )
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            text = (safe_text(loc, timeout=5000) or "").strip()
            if text:
                return text
            html = loc.inner_html(timeout=5000) or ""
            text = strip_html(html).strip()
            if text:
                return text
        except Exception:
            continue
    return ""


def extract_job_id_from_url(url):
    """
    Extract the LinkedIn job ID from a job URL.
    Tries multiple URL patterns to find the job ID.
    
    Args:
        url: LinkedIn job URL
        
    Returns:
        Job ID string or None if not found
    """
    if not url:
        return None
    match = re.search(r"/jobs/view/(\d+)", url)
    if match:
        return match.group(1)
    match = re.search(r"[?&]currentJobId=(\d+)", url)
    if match:
        return match.group(1)
    return None



def jobs_search_url(keyword, time_filter=None):
    """
    Build LinkedIn search URL for a given keyword.
    
    Args:
        keyword: Search keyword
        time_filter: LinkedIn f_TPR value (e.g. r86400). Default: config 24h.
        
    Returns:
        Full search URL with geo and time filters
    """
    encoded = quote_plus(keyword)
    tf = time_filter or TIME_FILTER
    return (
        f"{BASE_URL}"
        f"?keywords={encoded}&f_TPR={tf}&geoId={GEO_ID}"
        "&origin=JOB_SEARCH_PAGE_JOB_FILTER&refresh=true"
    )


def get_status(job):
    """
    Extract job application status from LinkedIn job card.
    
    Args:
        job: Playwright locator for a job card element
        
    Returns:
        Status text or "new" as default
    """
    text = safe_text(job.locator(".job-card-container__footer-job-state"))
    return text or "new"


def scroll_job_list(page):
    """
    Scroll the LinkedIn job list to load more cards.
    LinkedIn uses virtual scrolling, so this forces more cards to render.
    
    Args:
        page: Playwright page object
    """
    print("Scrolling the job list to load more cards...")
    list_container = page.locator(".scaffold-layout__list").first
    last_count = 0
    for i in range(5):
        cards = page.locator(JOB_CARD_SELECTOR)
        count = cards.count()
        if count > 0:
            cards.last.scroll_into_view_if_needed()
        elif list_container.count() > 0:
            list_container.evaluate("el => { el.scrollTop = el.scrollHeight }")
        else:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        pause(1.5, 2.5)
        new_count = page.locator(JOB_CARD_SELECTOR).count()
        print(f"Scroll {i + 1}: {last_count} -> {new_count} cards")
        if new_count <= last_count:
            print("Card count did not increase, stop scrolling")
            break
        last_count = new_count
    pause(2, 4, "Waiting for the list to finish rendering")


def _sdui_job_id(component_key: str) -> str:
    prefix = "job-card-component-ref-"
    if component_key and component_key.startswith(prefix):
        return component_key[len(prefix):]
    return ""


def _sdui_card_fields(card):
    """
    Read title, company, location, and job id from one SDUI result card.

    Title text is often duplicated, and a verified badge is glued onto the
    first copy. Company and location are the next plain paragraphs.
    """
    job_id = _sdui_job_id(card.get_attribute("componentkey") or "")
    try:
        spans = [
            text.strip()
            for text in card.locator("span").all_inner_texts()
            if text and text.strip()
        ]
    except Exception:
        spans = []

    title = ""
    for text in spans:
        if text.startswith("Posted ") or text.endswith(" ago"):
            break
        cleaned = text.replace(" (Verified job)", "").strip()
        if not cleaned or "alumni" in cleaned.lower() or "EUR/" in cleaned:
            continue
        if not title or len(cleaned) < len(title):
            title = cleaned

    try:
        paragraphs = [
            text.strip()
            for text in card.locator("p").all_inner_texts()
            if text and text.strip()
        ]
    except Exception:
        paragraphs = []

    details = []
    for text in paragraphs:
        if title and title in text:
            continue
        if (
            text in {"·", "Promoted", "Easy Apply"}
            or text.startswith("Be an early")
            or text.startswith("Posted ")
            or "alumni" in text.lower()
            or "EUR/" in text
        ):
            continue
        details.append(text)

    company = details[0] if details else ""
    location = details[1] if len(details) > 1 else ""
    return title, company, location, job_id


def _save_open_linkedin_job(
    page, index, title, company, location, status, href_value, job_id, jobs_data
):
    """Read the already-open detail panel and save the job when it passes gates."""
    apply_number = safe_text(
        page.locator(".job-details-jobs-unified-top-card__tertiary-description-container")
    )

    job_desc_text = ""
    for attempt in range(1, DESCRIPTION_FETCH_RETRIES + 1):
        job_desc_text = extract_linkedin_description(page, job_id)
        if is_usable_job_description(job_desc_text):
            break
        if attempt < DESCRIPTION_FETCH_RETRIES:
            print(
                f"Job {index + 1}: empty description, "
                f"retry {attempt}/{DESCRIPTION_FETCH_RETRIES - 1}"
            )
            pause(1.5, 3.0)
            try:
                page.wait_for_selector(_LINKEDIN_DETAIL_WAIT, timeout=8000)
            except PlaywrightTimeoutError:
                pass

    desc_empty = not is_usable_job_description(job_desc_text)
    if desc_empty:
        print(
            f"Job {index + 1}: description still empty after "
            f"{DESCRIPTION_FETCH_RETRIES} attempts — save anyway "
            f"(description_empty; AI will skip)"
        )
        job_data = {
            "title": title,
            "company": company,
            "location": location,
            "status": status,
            "link": href_value,
            "job_id": job_id,
            "applicants": apply_number,
            "description": job_desc_text or "",
            "description_empty": True,
            "source": SOURCE,
        }
        if save_job(job_data):
            jobs_data.append(job_data)
            print(f"Job {index + 1}: saved job_id {job_id} (empty description)")
    else:
        should_skip, lang, german_share = is_non_english_job_detail(job_desc_text)
        print(
            f"Job {index + 1}: description length {len(job_desc_text)}, "
            f"language={lang or 'unknown'}, german_share={german_share:.0%}"
        )

        if should_skip:
            increment_scraper_stat("german_filtered")
            print(
                f"Job {index + 1}: Non-English description ({lang}) detected, "
                f"skip save (german_filtered)"
            )
        else:
            job_data = {
                "title": title,
                "company": company,
                "location": location,
                "status": status,
                "link": href_value,
                "job_id": job_id,
                "applicants": apply_number,
                "description": job_desc_text,
                "description_empty": False,
                "source": SOURCE,
            }

            if save_job(job_data):
                jobs_data.append(job_data)
                print(f"Job {index + 1}: saved job_id {job_id}")

    if random.random() < 0.3:
        pause(1, 3, f"Job {index + 1}: extra think time")
    pause(4, 8, f"Job {index + 1}: wait before the next card")


def scrape_sdui_jobs(page, max_jobs=MAX_JOBS_PER_PAGE, keyword=""):
    """
    Scrape the SDUI /jobs/search-results/ list.

    Cards are role=button nodes whose componentkey is
    job-card-component-ref-<job id>. Each id is also rendered once without
    the button role, so only the button nodes are clicked.
    """
    page.wait_for_selector(SDUI_JOB_CARD_SELECTOR, timeout=15000)
    total_cards = page.locator(SDUI_JOB_CARD_SELECTOR).count()
    limit = min(max_jobs, total_cards)
    jobs_data = []
    print(f"Found {total_cards} SDUI job cards, processing the first {limit}")

    for index in range(limit):
        print(f"\n=== Job {index + 1}/{limit} ===")
        title = ""
        job_id = ""
        href_value = ""
        company = ""
        location = ""
        title_passed = False
        try:
            cards = page.locator(SDUI_JOB_CARD_SELECTOR)
            if index >= cards.count():
                print(f"Job {index + 1}: index out of range, skip")
                continue

            job = cards.nth(index)
            job.scroll_into_view_if_needed()
            title, company, location, job_id = _sdui_card_fields(job)
            href_value = f"https://www.linkedin.com/jobs/view/{job_id}/" if job_id else ""
            print(f"Job {index + 1}: {title} | {company} | {location}")

            if not job_id:
                print(f"Job {index + 1}: could not extract job_id, skip")
                continue

            skip, reason = should_skip_title_before_click(title)
            if skip:
                if "AI judged unrelated" in reason:
                    increment_scraper_stat("ai_title_filtered")
                print(f"Job {index + 1}: {reason}, skip → {title}")
                continue

            if is_job_id_exists(job_id, SOURCE):
                print(f"Job {index + 1}: job_id {job_id} already in DB, skip click")
                continue

            title_passed = True
            increment_scraper_stat("title_passed_clicked")
            job.click()
            page.wait_for_selector(
                f'[componentkey="JobDetails_AboutTheJob_{job_id}"], {_LINKEDIN_DETAIL_WAIT}',
                timeout=15000,
            )
            pause(1.0, 2.0)
            _save_open_linkedin_job(
                page,
                index,
                title,
                company,
                location,
                "new",
                href_value,
                job_id,
                jobs_data,
            )
        except PlaywrightTimeoutError:
            print(f"Job {index + 1}: timed out while loading details")
            if title_passed:
                save_timeout_job(
                    title=title,
                    job_id=job_id,
                    link=href_value,
                    source=SOURCE,
                    company=company,
                    location=location,
                    keyword=keyword,
                    reason="detail_timeout",
                )
        except Exception as e:
            print(f"Job {index + 1}: error {type(e).__name__}: {e}")
            continue

    print(f"\n=== Page done, saved {len(jobs_data)} new jobs ===")
    return jobs_data


def scrape_jobs(page, max_jobs=MAX_JOBS_PER_PAGE, keyword=""):
    """
    Scrape job listings from the current LinkedIn search results page.
    
    Args:
        page: Playwright page object
        max_jobs: Maximum number of jobs to process per page
        
    Returns:
        List of job data dictionaries that were successfully saved
    """
    try:
        page.wait_for_selector(
            f"{SDUI_JOB_CARD_SELECTOR}, {JOB_CARD_SELECTOR}",
            timeout=15000,
        )
    except PlaywrightTimeoutError:
        print("No job cards found")
        return []

    if page.locator(SDUI_JOB_CARD_SELECTOR).count() > 0:
        return scrape_sdui_jobs(page, max_jobs=max_jobs, keyword=keyword)

    scroll_job_list(page)
    page.wait_for_selector(JOB_CARD_SELECTOR, timeout=15000)

    total_cards = page.locator(JOB_CARD_SELECTOR).count()
    limit = min(max_jobs, total_cards)
    jobs_data = []
    print(f"Found {total_cards} job cards, processing the first {limit}")

    for index in range(limit):
        print(f"\n=== Job {index + 1}/{limit} ===")
        title = ""
        job_id = ""
        href_value = ""
        company = ""
        location = ""
        title_passed = False
        try:
            cards = page.locator(JOB_CARD_SELECTOR)
            if index >= cards.count():
                print(f"Job {index + 1}: index out of range, skip")
                continue

            job = cards.nth(index)
            job.scroll_into_view_if_needed()
            job.locator(JOB_LINK_SELECTOR).first.wait_for(state="visible", timeout=10000)

            link = job.locator(JOB_LINK_SELECTOR).first
            title = (link.get_attribute("aria-label") or link.inner_text() or "").strip()
            href_value = link.get_attribute("href") or ""
            company = safe_text(job.locator(".artdeco-entity-lockup__subtitle span"))
            location = safe_text(job.locator(".job-card-container__metadata-wrapper li span"))
            status = get_status(job)

            print(f"Job {index + 1}: {title} | {company} | {location}")

            job_id = extract_job_id_from_url(href_value)
            if not job_id:
                print(f"Job {index + 1}: could not extract job_id, skip")
                continue

            skip, reason = should_skip_title_before_click(title)
            if skip:
                if "AI judged unrelated" in reason:
                    increment_scraper_stat("ai_title_filtered")
                print(f"Job {index + 1}: {reason}, skip → {title}")
                continue

            if is_job_id_exists(job_id, SOURCE):
                print(f"Job {index + 1}: job_id {job_id} already in DB, skip click")
                continue

            title_passed = True
            increment_scraper_stat("title_passed_clicked")
            job.click()
            page.wait_for_selector(_LINKEDIN_DETAIL_WAIT, timeout=15000)
            pause(1.0, 2.0)
            _save_open_linkedin_job(
                page,
                index,
                title,
                company,
                location,
                status,
                href_value,
                job_id,
                jobs_data,
            )

        except PlaywrightTimeoutError:
            print(f"Job {index + 1}: timed out while loading details")
            if title_passed:
                save_timeout_job(
                    title=title,
                    job_id=job_id,
                    link=href_value,
                    source=SOURCE,
                    company=company,
                    location=location,
                    keyword=keyword,
                    reason="detail_timeout",
                )
        except Exception as e:
            print(f"Job {index + 1}: error {type(e).__name__}: {e}")
            continue

    print(f"\n=== Page done, saved {len(jobs_data)} new jobs ===")
    return jobs_data


def go_to_next_page(page):
    """
    Navigate to the next page of LinkedIn search results.
    
    Args:
        page: Playwright page object
        
    Returns:
        True if successfully navigated to next page, False otherwise
    """
    next_btn = page.locator('button[aria-label="View next page"]')
    classic = next_btn.count() > 0
    if not classic:
        next_btn = page.locator(SDUI_NEXT_PAGE_SELECTOR)
    if next_btn.count() == 0:
        print("No next-page button, stop paging")
        return False
    if classic:
        try:
            if not next_btn.first.is_enabled():
                print("No next-page button, stop paging")
                return False
        except Exception:
            print("No next-page button, stop paging")
            return False
    next_btn.first.scroll_into_view_if_needed()
    pause(0.4, 0.8)
    next_btn.first.click()
    pause(5, 10, "Waiting after pagination")
    return True


def scrape_keyword(
    page,
    keyword,
    max_pages,
    max_jobs_per_page=LINKEDIN_JOBS_PER_PAGE,
    time_filter=None,
):
    """
    Scrape multiple pages of LinkedIn results for a single keyword.
    
    Args:
        page: Playwright page object
        keyword: Search keyword
        max_pages: Maximum number of pages to scrape
        max_jobs_per_page: Maximum jobs to process per page
        time_filter: LinkedIn f_TPR value (e.g. r172800 for 48h)
        
    Returns:
        List of all job data dictionaries saved for this keyword
    """
    print(f"\n========== Keyword: {keyword} ==========")
    goto_page(page, jobs_search_url(keyword, time_filter=time_filter))
    pause(4, 7, f"Waiting for search results: {keyword}")

    all_jobs = []
    for page_index in range(max_pages):
        print(f"\n--- {keyword}: page {page_index + 1}/{max_pages} ---")
        jobs = scrape_jobs(page, max_jobs=max_jobs_per_page, keyword=keyword)
        all_jobs.extend(jobs)
        if page_index < max_pages - 1:
            if not go_to_next_page(page):
                break
            pause(15, 30, "Rest between pages")
    return all_jobs


def main():
    args = parse_args()
    keywords = args.keywords
    max_pages = args.max_pages
    max_jobs = args.max_jobs if args.max_jobs is not None else LINKEDIN_JOBS_PER_PAGE
    time_filter = resolve_time_filter(args.hours)
    hours_label = (
        f"{args.hours:g}h (--hours)"
        if args.hours is not None
        else f"config ({time_filter})"
    )
    print(f"Keywords: {keywords}")
    print(f"Max pages per keyword: {max_pages}")
    print(f"Max jobs per page: {max_jobs}")
    print(f"Time window: {hours_label} → f_TPR={time_filter}")

    init_db()
    all_jobs = []

    with sync_playwright() as playwright:
        browser = connect_browser(playwright, "LinkedIn")
        context = browser.contexts[0]
        page = open_scraper_page(context, bring_to_front=True)
        print("Log in to LinkedIn in the debug Chrome window if you have not already.")

        for i, keyword in enumerate(keywords):
            jobs = scrape_keyword(
                page,
                keyword,
                max_pages,
                max_jobs_per_page=max_jobs,
                time_filter=time_filter,
            )
            all_jobs.extend(jobs)
            if i < len(keywords) - 1:
                pause(15, 30, "Rest between keywords")

        # Do not close the connected Chrome; it is the user's real session.

    print(f"\nDone. Saved {len(all_jobs)} new LinkedIn jobs this run.")


if __name__ == "__main__":
    main()
