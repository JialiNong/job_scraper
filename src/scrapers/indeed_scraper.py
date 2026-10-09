#!/usr/bin/env python3
"""
Indeed Job Scraper
Scrapes job listings from Indeed and saves them to MongoDB
"""
import sys
import os
from urllib.parse import quote_plus
import random

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
    INDEED_JOBS_PER_PAGE,
    INDEED_CONFIG,
    DEFAULT_MAX_PAGES,
)
from core.scraper_utils import (
    pause,
    safe_text,
    safe_attr,
    parse_args,
    connect_browser,
    open_scraper_page,
    should_skip_title_before_click,
    is_non_english_job_detail,
    is_usable_job_description,
    strip_html,
    normalize_indeed_job_id,
    extract_indeed_jk_from_url,
    extract_indeed_detail_meta,
    goto_page,
    is_target_closed_error,
    DESCRIPTION_FETCH_RETRIES,
)
from core.challenge_wait import (
    handle_indeed_challenge_if_needed,
    is_indeed_challenge_page,
)

# Indeed configuration
BASE_URL = INDEED_CONFIG["base_url"]
FROMAGE = INDEED_CONFIG["fromage"]
LOCATION = INDEED_CONFIG.get("location") or ""
SORT = INDEED_CONFIG.get("sort") or ""
SOURCE = INDEED_CONFIG["source"]
RESULTS_PER_PAGE = INDEED_CONFIG["results_per_page"]

# Indeed selectors
JOB_CARD_SELECTOR = INDEED_CONFIG["selectors"]["job_card"]
JOB_TITLE_SELECTOR = INDEED_CONFIG["selectors"]["job_title"]
DETAIL_SELECTOR = INDEED_CONFIG["selectors"]["detail"]

# Human-like pacing (Indeed is stricter than LinkedIn about bot-like click trains)
_PRE_CLICK_PAUSE = (1.8, 4.5)
_POST_CLICK_PAUSE = (1.5, 3.5)
_BETWEEN_CARDS_PAUSE = (5.0, 11.0)
_BETWEEN_PAGES_PAUSE = (35.0, 80.0)
_BETWEEN_KEYWORDS_PAUSE = (30.0, 70.0)
_CLICK_HOLD_MS = (80, 220)


def _human_card_click(card) -> None:
    """Click a job card with a short mousedown→mouseup delay (less mechanical)."""
    delay_ms = random.randint(*_CLICK_HOLD_MS)
    card.click(delay=delay_ms)


def _reload_after_challenge(page) -> None:
    """Reload SERP and give the page a moment after the user clears a challenge."""
    try:
        page.reload(wait_until="domcontentloaded")
    except Exception as exc:
        print(f"⚠ Reload after challenge failed: {exc}")
    pause(3.0, 6.0, "Settle after challenge / reload")
    dismiss_overlays(page)


def _wait_for_job_cards(page, *, context: str, timeout_ms: int = 15000) -> bool:
    """
    Wait for SERP job cards. On challenge pages, Telegram-wait for the user,
    reload, and retry once. Returns True if cards are present.
    """
    for attempt in range(2):
        try:
            page.wait_for_selector(JOB_CARD_SELECTOR, timeout=timeout_ms)
            return True
        except PlaywrightTimeoutError:
            pass

        if not is_indeed_challenge_page(page):
            return False

        label = context if attempt == 0 else f"{context}, still blocked"
        handle_indeed_challenge_if_needed(
            page, context=label, ready_selector=JOB_CARD_SELECTOR
        )
        _reload_after_challenge(page)

    try:
        page.wait_for_selector(JOB_CARD_SELECTOR, timeout=timeout_ms)
        return True
    except PlaywrightTimeoutError:
        return False


def extract_indeed_description(page, detail) -> str:
    """Pull JD text from the Indeed detail panel via known selectors."""
    description = ""
    for sel in (
        "#jobDescriptionText",
        ".jobsearch-JobComponent-description",
        ".simple-job-description-html",
        DETAIL_SELECTOR,
    ):
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            html = loc.inner_html(timeout=10000) or ""
            description = strip_html(html)
            if description:
                return description
        except Exception:
            continue
    try:
        return strip_html(detail.inner_html(timeout=10000) or "")
    except Exception:
        return description


def jobs_search_url(keyword, start=0):
    """
    Build Indeed search URL for a given keyword and pagination offset.

    Hyphens in keywords become spaces in `q=` so "full-stack" searches as
    "Full Stack", matching the desktop SERP (`q=Full+Stack&l=&fromage=1`).
    """
    query = (keyword or "").replace("-", " ").strip()
    encoded = quote_plus(query)
    loc = quote_plus(LOCATION) if LOCATION else ""
    url = f"{BASE_URL}?q={encoded}&l={loc}&fromage={FROMAGE}&from=searchOnDesktopSerp"
    if SORT:
        url += f"&sort={quote_plus(SORT)}"
    if start > 0:
        url += f"&start={start}"
    return url


def dismiss_overlays(page):
    """Best-effort dismiss cookie / consent dialogs on Indeed DE."""
    candidates = [
        'button#onetrust-accept-btn-handler',
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


def extract_card_fields(card):
    """
    Extract title, job_id, and URL from an Indeed job card.
    
    Title resolution order:
      1. `title` attribute on the <span> inside h3.jobTitle  (most reliable)
      2. Inner text of that <span>
      3. Inner text of the whole h3.jobTitle heading

    Args:
        card: Playwright locator for a job card element
        
    Returns:
        Tuple of (title, job_id, href)
    """
    title_heading = card.locator(JOB_TITLE_SELECTOR).first
    span = title_heading.locator("span").first
    # Prefer the span's title attribute (Indeed DE sets it reliably)
    title = safe_attr(span, "title") or safe_text(span)
    if not title:
        title = safe_text(title_heading)

    link = title_heading.locator("a").first
    href = safe_attr(link, "href")
    if href and href.startswith("/"):
        href = f"https://de.indeed.com{href}"

    # Prefer jk= from the href; fall back to DOM id (job_ / sj_ prefix stripped).
    job_id = extract_indeed_jk_from_url(href or "")
    if not job_id:
        job_id = normalize_indeed_job_id(safe_attr(link, "id") or "")

    if not href and job_id:
        href = f"https://de.indeed.com/viewjob?jk={job_id}"

    return title, job_id, href


class _IndeedSession:
    """CDP session that can reopen a tab if Chrome dropped the previous one."""

    def __init__(self, playwright):
        self.playwright = playwright
        self.browser = None
        self.page = None
        self.connect(announce=True)

    def connect(self, *, announce: bool = False) -> None:
        self.browser = connect_browser(self.playwright, "Indeed")
        context = self.browser.contexts[0]
        self.page = open_scraper_page(context)
        if announce:
            print(
                "Use the debug Chrome window. "
                "Dismiss Indeed cookie banners if prompted."
            )

    def ensure(self) -> bool:
        """Reconnect if the tab is gone. Returns True when a new tab was opened."""
        try:
            if self.page is not None and not self.page.is_closed():
                _ = self.page.url
                return False
        except Exception:
            pass
        print("⚠ Indeed tab/browser was closed — reconnecting to debug Chrome…")
        self.connect()
        return True


def _goto_serp(session: _IndeedSession, url: str) -> None:
    """Navigate to a SERP; reconnect once if the tab died mid-goto."""
    last_error = None
    for attempt in range(1, 4):
        session.ensure()
        try:
            goto_page(session.page, url)
            return
        except Exception as exc:
            last_error = exc
            if is_target_closed_error(exc) and attempt < 3:
                print(
                    f"⚠ Tab closed during navigation "
                    f"(attempt {attempt}/3) — reconnecting"
                )
                session.connect()
                continue
            raise
    raise last_error


def scrape_jobs(page, max_jobs=INDEED_JOBS_PER_PAGE, keyword=""):
    """
    Scrape job listings from the current Indeed search results page.
    
    Args:
        page: Playwright page object
        max_jobs: Maximum number of jobs to process per page
        keyword: Search keyword (stored on timeout review rows)
        
    Returns:
        List of job data dictionaries that were successfully saved
    """
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
        title_passed = False
        try:
            cards = page.locator(JOB_CARD_SELECTOR)
            if index >= cards.count():
                print(f"Job {index + 1}: index out of range, skip")
                continue

            card = cards.nth(index)
            try:
                card.scroll_into_view_if_needed()
            except PlaywrightTimeoutError:
                print(f"Job {index + 1}: scroll timed out, still reading card")
            pause(0.4, 1.2)
            title, job_id, href_value = extract_card_fields(card)
            print(f"Job {index + 1}: {title} | id={job_id}")

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
            # Title passed + new job → count as a candidate we evaluated
            increment_scraper_stat("title_passed_clicked")
            pause(*_PRE_CLICK_PAUSE, f"Job {index + 1}: think before click")
            _human_card_click(card)

            try:
                page.wait_for_selector(DETAIL_SELECTOR, timeout=15000)
            except PlaywrightTimeoutError:
                if is_indeed_challenge_page(page):
                    handle_indeed_challenge_if_needed(
                        page,
                        context=f"after click job {index + 1}",
                        ready_selector=JOB_CARD_SELECTOR,
                    )
                    pause(2.0, 4.0, "Settle after challenge")
                    # Re-click the same card if the SERP is still open
                    try:
                        cards = page.locator(JOB_CARD_SELECTOR)
                        if index < cards.count():
                            pause(*_PRE_CLICK_PAUSE, f"Job {index + 1}: re-click after challenge")
                            _human_card_click(cards.nth(index))
                        page.wait_for_selector(DETAIL_SELECTOR, timeout=15000)
                    except PlaywrightTimeoutError:
                        print(f"Job {index + 1}: detail still missing after challenge")
                        save_timeout_job(
                            title=title,
                            job_id=job_id,
                            link=href_value,
                            source=SOURCE,
                            keyword=keyword,
                            reason="detail_timeout",
                        )
                        continue
                else:
                    print(f"Job {index + 1}: timed out while loading details")
                    save_timeout_job(
                        title=title,
                        job_id=job_id,
                        link=href_value,
                        source=SOURCE,
                        keyword=keyword,
                        reason="detail_timeout",
                    )
                    continue

            pause(*_POST_CLICK_PAUSE)

            detail = page.locator(DETAIL_SELECTOR).first
            description = ""
            for attempt in range(1, DESCRIPTION_FETCH_RETRIES + 1):
                detail = page.locator(DETAIL_SELECTOR).first
                description = extract_indeed_description(page, detail)
                if is_usable_job_description(description):
                    break
                if attempt < DESCRIPTION_FETCH_RETRIES:
                    print(
                        f"Job {index + 1}: empty description, "
                        f"retry {attempt}/{DESCRIPTION_FETCH_RETRIES - 1}"
                    )
                    pause(1.5, 3.0)
                    try:
                        page.wait_for_selector(DETAIL_SELECTOR, timeout=8000)
                    except PlaywrightTimeoutError:
                        pass

            company, location = extract_indeed_detail_meta(page)
            desc_empty = not is_usable_job_description(description)
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
                    "status": "new",
                    "link": href_value,
                    "job_id": job_id,
                    "applicants": "",
                    "description": description or "",
                    "description_empty": True,
                    "source": SOURCE,
                }
                if save_job(job_data):
                    jobs_data.append(job_data)
                    print(f"Job {index + 1}: saved job_id {job_id} (empty description)")
            else:
                should_skip, lang, german_share = is_non_english_job_detail(description)
                print(
                    f"Job {index + 1}: description length {len(description)}, "
                    f"company={company or '(none)'}, location={location or '(none)'}, "
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
                        "status": "new",
                        "link": href_value,
                        "job_id": job_id,
                        "applicants": "",
                        "description": description,
                        "description_empty": False,
                        "source": SOURCE,
                    }

                    if save_job(job_data):
                        jobs_data.append(job_data)
                        print(f"Job {index + 1}: saved job_id {job_id}")

            if random.random() < 0.35:
                pause(2, 6, f"Job {index + 1}: extra think time")
            pause(*_BETWEEN_CARDS_PAUSE, f"Job {index + 1}: wait before the next card")

        except PlaywrightTimeoutError:
            if is_indeed_challenge_page(page):
                try:
                    handle_indeed_challenge_if_needed(
                        page,
                        context=f"timeout on job {index + 1}",
                        ready_selector=JOB_CARD_SELECTOR,
                    )
                except RuntimeError as exc:
                    print(f"Job {index + 1}: {exc}")
                    break
            else:
                print(f"Job {index + 1}: timed out while loading details")
            if title_passed:
                save_timeout_job(
                    title=title,
                    job_id=job_id,
                    link=href_value,
                    source=SOURCE,
                    keyword=keyword,
                    reason="detail_timeout",
                )
        except RuntimeError as e:
            # Challenge resume timeout — abort the page loop
            print(f"Job {index + 1}: {e}")
            break
        except Exception as e:
            if is_target_closed_error(e):
                print(
                    f"Job {index + 1}: tab/browser closed "
                    f"({type(e).__name__}) — will reconnect and retry this page"
                )
                raise
            print(f"Job {index + 1}: error {type(e).__name__}: {e}")
            continue

    print(f"\n=== Page done, saved {len(jobs_data)} new jobs ===")
    return jobs_data


def scrape_keyword(
    session: _IndeedSession,
    keyword,
    max_pages,
    max_jobs_per_page=INDEED_JOBS_PER_PAGE,
):
    """
    Scrape multiple pages of Indeed results for a single keyword.

    Args:
        session: Live Indeed CDP session (reopens the tab if Chrome dropped it)
        keyword: Search keyword
        max_pages: Maximum number of pages to scrape
        max_jobs_per_page: Maximum jobs to process per page

    Returns:
        List of all job data dictionaries saved for this keyword
    """
    print(f"\n========== Keyword: {keyword} ==========")
    all_jobs = []
    page = session.page

    for page_index in range(max_pages):
        start = page_index * RESULTS_PER_PAGE
        print(f"\n--- {keyword}: page {page_index + 1}/{max_pages} (start={start}) ---")
        url = jobs_search_url(keyword, start=start)
        print(f"URL: {url}")
        cards_ready = False
        try:
            _goto_serp(session, url)
        except Exception as exc:
            print(f"{keyword}: navigation failed ({type(exc).__name__}: {exc})")
            break
        page = session.page
        pause(4, 8, f"Waiting for search results: {keyword}")
        dismiss_overlays(page)

        try:
            cards_ready = _wait_for_job_cards(
                page, context=f"{keyword} page {page_index + 1}"
            )
        except RuntimeError as exc:
            print(f"{keyword}: {exc}")
            break
        except Exception as exc:
            if is_target_closed_error(exc):
                print(
                    f"{keyword}: tab closed while waiting for cards — "
                    "reconnect and retry this page"
                )
                session.connect()
                try:
                    _goto_serp(session, url)
                    page = session.page
                    pause(4, 8, f"Waiting for search results: {keyword}")
                    dismiss_overlays(page)
                    cards_ready = _wait_for_job_cards(
                        page, context=f"{keyword} page {page_index + 1} retry"
                    )
                except Exception as retry_exc:
                    print(
                        f"{keyword}: retry failed "
                        f"({type(retry_exc).__name__}: {retry_exc})"
                    )
                    break
            else:
                print(f"{keyword}: {type(exc).__name__}: {exc}")
                break

        if not cards_ready:
            print(f"No job cards for {keyword} on page {page_index + 1}, stop paging")
            break

        try:
            jobs = scrape_jobs(page, max_jobs=max_jobs_per_page, keyword=keyword)
        except Exception as exc:
            if not is_target_closed_error(exc):
                raise
            print(
                f"{keyword}: tab closed mid-page — reconnect and retry "
                f"page {page_index + 1}"
            )
            session.connect()
            try:
                _goto_serp(session, url)
                page = session.page
                pause(4, 8, f"Waiting for search results: {keyword}")
                dismiss_overlays(page)
                if not _wait_for_job_cards(
                    page, context=f"{keyword} page {page_index + 1} retry"
                ):
                    print(
                        f"No job cards for {keyword} on page {page_index + 1} "
                        "after reconnect, stop paging"
                    )
                    break
                jobs = scrape_jobs(page, max_jobs=max_jobs_per_page, keyword=keyword)
            except Exception as retry_exc:
                print(
                    f"{keyword}: retry failed "
                    f"({type(retry_exc).__name__}: {retry_exc})"
                )
                break

        all_jobs.extend(jobs)
        page = session.page

        if page_index < max_pages - 1:
            pause(*_BETWEEN_PAGES_PAUSE, "Rest between pages")

    return all_jobs


def main():
    args = parse_args()
    keywords = args.keywords
    max_pages = args.max_pages if args.max_pages is not None else DEFAULT_MAX_PAGES
    max_jobs = args.max_jobs if args.max_jobs is not None else INDEED_JOBS_PER_PAGE
    print(f"Source: {SOURCE}")
    print(f"Keywords: {keywords}")
    print(f"Location: {LOCATION or '(empty)'}  sort={SORT or 'relevance'}  fromage={FROMAGE}")
    print(f"Max pages per keyword: {max_pages}")
    print(f"Max jobs per page: {max_jobs}")

    init_db()
    all_jobs = []

    with sync_playwright() as playwright:
        session = _IndeedSession(playwright)

        for i, keyword in enumerate(keywords):
            jobs = scrape_keyword(
                session, keyword, max_pages, max_jobs_per_page=max_jobs
            )
            all_jobs.extend(jobs)
            if i < len(keywords) - 1:
                pause(*_BETWEEN_KEYWORDS_PAUSE, "Rest between keywords")

    print(f"\nDone. Saved {len(all_jobs)} new Indeed jobs this run.")


if __name__ == "__main__":
    main()
