"""
Shared utility functions for web scrapers.
Provides common functionality for browser automation, delays, and data extraction.
"""
import argparse
import html as html_module
import json
import os
import random
import re
import socket
import subprocess
import time

from lingua import Language, LanguageDetectorBuilder

from core.config import (
    DEFAULT_KEYWORDS,
    CDP_HOST,
    CDP_PORT,
    CDP_URL,
    CHROME_BIN,
    CHROME_USER_DATA_DIR,
    TITLE_EXCLUDE_KEYWORDS,
)

# Pre-compile all exclusion patterns once for efficiency (case-insensitive)
_EXCLUDE_PATTERNS = [re.compile(p, re.IGNORECASE) for p in TITLE_EXCLUDE_KEYWORDS]

# Normalize hyphens/underscores so "full-stack" matches "Full Stack"
_KEYWORD_SPLIT = re.compile(r"[-_\s]+")

# Indeed job keys appear as jk= (viewjob) or vjk= (SERP selected card),
# and as DOM ids with job_/sj_ prefixes.
_INDEED_JK_RE = re.compile(r"[?&]jk=([a-zA-Z0-9]+)")
_INDEED_VJK_RE = re.compile(r"[?&]vjk=([a-zA-Z0-9]+)")
_INDEED_ID_PREFIXES = ("job_", "sj_")


def normalize_indeed_job_id(raw: str) -> str:
    """Strip Indeed DOM id prefixes (job_ / sj_) down to the bare job key."""
    if not raw:
        return ""
    value = str(raw).strip()
    for prefix in _INDEED_ID_PREFIXES:
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def indeed_job_id_variants(job_id: str) -> list:
    """Bare jk plus legacy prefixed forms for DB lookups / dedup."""
    bare = normalize_indeed_job_id(job_id)
    if not bare:
        return []
    return [bare, f"job_{bare}", f"sj_{bare}"]


def extract_indeed_jk_from_url(url: str) -> str:
    """
    Extract the Indeed job key from a URL.

    Prefers jk= (viewjob / rc/clk); falls back to vjk= (SERP selected card).
    Returns the bare key only (no jk_/vjk_ prefix).
    """
    if not url:
        return ""
    m = _INDEED_JK_RE.search(url)
    if m:
        return m.group(1)
    m = _INDEED_VJK_RE.search(url)
    return m.group(1) if m else ""


_INDEED_LOCATION_SKIP = {
    "vollzeit",
    "teilzeit",
    "full-time",
    "part-time",
    "full time",
    "part time",
    "hybrides arbeiten",
    "hybrid",
    "remote",
    "homeoffice",
    "home office",
    "befristet",
    "unbefristet",
}

# Indeed metadata often uses middle-dot / bullet separators between company,
# location, and work-mode. Those characters alone must never become location.
_INDEED_META_SEP_RE = re.compile(r"[•·∙⋅|]+")
_INDEED_SEP_ONLY_RE = re.compile(r"^[\s•·∙⋅|\-\u2013\u2014]+$")

# Company rating chip next to the company name (e.g. "3.8", "4,2", "3.8 ★",
# "3.8 (152)"). That token sits on the company row; the real address is the
# next line — never treat the score as location.
_INDEED_RATING_RE = re.compile(
    r"^[0-5](?:[.,]\d)?\s*(?:★|⭐)?\s*(?:\([\d.,\s]+\))?$",
    re.IGNORECASE,
)


def _indeed_meta_parts(text: str) -> list:
    """Split a metadata line on Indeed bullet separators; drop empties."""
    if not text:
        return []
    parts = []
    for chunk in _INDEED_META_SEP_RE.split(text):
        chunk = chunk.strip()
        if chunk and not _INDEED_SEP_ONLY_RE.match(chunk):
            parts.append(chunk)
    return parts


def _is_indeed_company_rating(text: str) -> bool:
    """True for Indeed company-rating chips like '3.8' / '4,2 ★'."""
    if not text:
        return False
    return bool(_INDEED_RATING_RE.match(text.strip()))


def _is_plausible_indeed_location(text: str, company: str) -> bool:
    """Reject separators, ratings, company name echoes, and work-mode chips."""
    if not text or _INDEED_SEP_ONLY_RE.match(text):
        return False
    if _is_indeed_company_rating(text):
        return False
    if company and text == company:
        return False
    # "Acme Corp 3.8" when company link text is "Acme Corp"
    if company and text.startswith(company):
        rest = text[len(company):].strip(" \t•·∙⋅|-–—")
        if not rest or _is_indeed_company_rating(rest):
            return False
    if text.lower() in _INDEED_LOCATION_SKIP:
        return False
    return True


def extract_indeed_detail_meta(scope) -> tuple:
    """
    Pull company + location from an Indeed job-detail panel.

    Indeed puts these in the sticky/compact header
    ([data-testid="company-info-metadata"]), not in the JD body —
    so scrapers that only store description leave them empty.

    Typical layout:
      1. job title
      2. company name (+ optional rating chip like 3.8)
      3. location / address

    The rating must never be stored as location.

    Args:
        scope: Playwright Page or Locator that contains the detail panel

    Returns:
        (company, location) — either may be ""
    """
    company = (
        safe_text(scope.locator('[data-testid="company-info-metadata"] a[href*="/cmp/"]'))
        or safe_text(scope.locator('[data-testid="desktop-job-header"] a[href*="/cmp/"]'))
        or safe_text(scope.locator('[data-testid="desktop-embedded-compact-header"] a[href*="/cmp/"]'))
        or safe_text(scope.locator('a[href*="/cmp/"]'))
    )

    location = ""

    # Dedicated location nodes when Indeed exposes them
    for sel in (
        '[data-testid="job-location"]',
        '[data-testid="inlineHeader-companyLocation"]',
        '[data-testid="jobsearch-JobInfoHeader-companyLocation"]',
        '[data-testid="company-info-metadata"] [data-testid="job-location"]',
    ):
        try:
            raw = safe_text(scope.locator(sel))
        except Exception:
            raw = ""
        for part in _indeed_meta_parts(raw) or ([raw.strip()] if raw and raw.strip() else []):
            if _is_plausible_indeed_location(part, company):
                location = part
                break
        if location:
            break

    # Compact header often has a single line: "Berlin · Hybrides Arbeiten"
    if not location:
        compact = scope.locator('[data-testid="desktop-embedded-compact-header"]')
        if compact.count() > 0:
            compact_text = safe_text(compact)
            for line in compact_text.splitlines():
                parts = _indeed_meta_parts(line)
                # Prefer the left-most non-company, non-rating, non-work-mode token
                for part in parts:
                    if _is_plausible_indeed_location(part, company):
                        location = part
                        break
                if location:
                    break

    if not location:
        meta = scope.locator('[data-testid="company-info-metadata"]')
        if meta.count() > 0:
            lines = [
                ln.strip()
                for ln in safe_text(meta).splitlines()
                if ln.strip() and not _INDEED_SEP_ONLY_RE.match(ln.strip())
            ]
            # Drop the company row (and a rating chip that rode on the same /
            # following line) so the next remaining line is the address.
            cleaned = []
            for ln in lines:
                if company and (ln == company or ln.startswith(company)):
                    rest = ln[len(company):].strip(" \t•·∙⋅|-–—") if ln.startswith(company) else ""
                    if rest and not _is_indeed_company_rating(rest) and _is_plausible_indeed_location(rest, company):
                        cleaned.append(rest)
                    continue
                if _is_indeed_company_rating(ln):
                    continue
                cleaned.append(ln)
            for ln in cleaned:
                parts = _indeed_meta_parts(ln) or [ln]
                for part in parts:
                    if _is_plausible_indeed_location(part, company):
                        location = part
                        break
                if location:
                    break

    # Final guard: never persist a bullet/separator/rating as location
    if location and not _is_plausible_indeed_location(location, company):
        location = ""

    return company, location


def scrub_bullet_location(location: str) -> str:
    """
    Clear location values that are only Indeed/UI separators or rating chips
    (e.g. '·' / '•' / '3.8').

    Safe for any source — a separator-only or rating-only string is never a
    real place.
    """
    text = (location or "").strip()
    if not text or _INDEED_SEP_ONLY_RE.match(text) or _is_indeed_company_rating(text):
        return ""
    return text

# Too little text to classify reliably (failed/empty detail panels).
_MIN_LANG_CHARS = 40

# Minimum usable JD length for AI matching. Shorter text is treated as empty
# (failed scrape / UI chrome only) — scrapers retry, then still save with
# description_empty=True; matcher skips AI and marks failed.
MIN_JOB_DESCRIPTION_CHARS = _MIN_LANG_CHARS
DESCRIPTION_FETCH_RETRIES = 3


def is_usable_job_description(text: str) -> bool:
    """
    True if the scraped job description has enough real content to match on.

    Empty / whitespace / tiny fragments fail. Scrapers still save those jobs
    with description_empty=True; the AI matcher must not score them.
    """
    plain = strip_html(text or "").strip()
    return len(plain) >= MIN_JOB_DESCRIPTION_CHARS

# Ignore tiny fragments ("Berlin", "3.8") when estimating language share.
_MIN_CHUNK_CHARS = 12

# Indeed DE chrome ("Weiter zur Bewerbung") is a few German lines on an
# English JD. Only treat the posting as German above this share.
GERMAN_SHARE_THRESHOLD = 0.20

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

# Restrict the model set to languages commonly seen on DE job boards.
# Lingua is more accurate with a small candidate set than with all 75 languages.
_LANGUAGE_DETECTOR = None


def is_title_excluded(title: str) -> bool:
    """
    Return True if the job title matches any exclusion pattern defined in
    TITLE_EXCLUDE_KEYWORDS, meaning the job card should be skipped without
    clicking into the detail page.

    Args:
        title: Job title string extracted from the card

    Returns:
        True if the title should be excluded, False otherwise
    """
    if not title:
        return False
    for pattern in _EXCLUDE_PATTERNS:
        if pattern.search(title):
            return True
    return False


def _normalize_title_text(text: str) -> str:
    """Lowercase and collapse hyphens/underscores/spaces for keyword matching."""
    return _KEYWORD_SPLIT.sub(" ", (text or "").lower()).strip()


def title_matches_default_keywords(title: str, keywords=None) -> bool:
    """
    Return True if the title contains any DEFAULT_KEYWORDS term
    (hyphen/space insensitive, e.g. full-stack ≈ fullstack ≈ full stack).
    """
    if not title:
        return False
    normalized_title = _normalize_title_text(title)
    # Also check a no-space form so "fullstack" matches "full stack"
    compact_title = normalized_title.replace(" ", "")
    for kw in keywords if keywords is not None else DEFAULT_KEYWORDS:
        normalized_kw = _normalize_title_text(kw)
        if not normalized_kw:
            continue
        if normalized_kw in normalized_title:
            return True
        if normalized_kw.replace(" ", "") in compact_title:
            return True
    return False


def is_title_relevant_by_ai(title: str, keywords=None) -> bool:
    """
    Ask AI whether a job title is related to the target roles in DEFAULT_KEYWORDS.

    Used when the title already passed TITLE_EXCLUDE_KEYWORDS but does not
    contain any DEFAULT_KEYWORDS term. Returns True if related (safe to click).

    On missing API key or API failure, returns True (fail open) so potentially
    good jobs are not dropped due to transient errors.
    """
    if not title:
        return False

    target_keywords = keywords if keywords is not None else DEFAULT_KEYWORDS
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("⚠ OPENAI_API_KEY missing; allowing click for title without keyword match")
        return True

    roles = ", ".join(target_keywords)
    prompt = f"""You screen job titles before opening the posting.

Target roles (keywords): {roles}

Job title: {title}

Is this title plausibly related to any of the target roles above?
Related examples: Software Developer, Frontend Developer, Full Stack Developer,
Web Engineer, GenAI Engineer, Product-minded Software Engineer, React Developer.
Unrelated examples: unrelated domains or roles not in the target list.

Respond ONLY with valid JSON:
{{"relevant": true/false, "reason": "<one short sentence>"}}
"""
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        model = os.getenv("AI_MODEL", "gpt-4.1-mini")
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You classify job titles against a fixed list of target "
                        "software roles. Be inclusive of close synonyms "
                        "(developer ≈ engineer, fullstack ≈ full-stack) but "
                        "reject clearly unrelated titles. Respond only with JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content or "{}")
        relevant = bool(result.get("relevant", False))
        reason = (result.get("reason") or "").strip()
        verdict = "relevant" if relevant else "unrelated"
        print(f"  AI title check: {verdict}" + (f" — {reason}" if reason else ""))
        return relevant
    except Exception as e:
        print(f"⚠ AI title relevance check failed ({e}); allowing click")
        return True


def should_skip_title_before_click(title: str) -> tuple:
    """
    Pre-click title gate.

    1. TITLE_EXCLUDE_KEYWORDS match → skip
    2. Contains a DEFAULT_KEYWORDS term → click
    3. Otherwise ask AI; skip only when AI says unrelated

    Returns:
        (should_skip: bool, reason: str)
    """
    if is_title_excluded(title):
        return True, "title excluded by filter"
    if title_matches_default_keywords(title):
        return False, "matches DEFAULT_KEYWORDS"
    if is_title_relevant_by_ai(title):
        return False, "AI judged relevant to target roles"
    return True, "AI judged unrelated to target roles"


def _get_language_detector():
    """Build a Lingua detector once; language models load lazily on first use."""
    global _LANGUAGE_DETECTOR
    if _LANGUAGE_DETECTOR is None:
        _LANGUAGE_DETECTOR = LanguageDetectorBuilder.from_languages(
            Language.ENGLISH,
            Language.GERMAN,
            Language.FRENCH,
            Language.DUTCH,
        ).build()
    return _LANGUAGE_DETECTOR


def strip_html(html_text: str) -> str:
    """
    Convert raw HTML to readable plain text.

    Removes style/script blocks and tags, decodes entities, and keeps
    paragraph/list breaks so the saved JD stays scannable.
    """
    if not html_text:
        return ""
    text = re.sub(
        r"<(style|script)[^>]*>.*?</\1>",
        "",
        html_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<(br|p|li|h[1-6]|div|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_module.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def german_content_share(text: str) -> float:
    """
    Fraction of classified text that Lingua labels as German (0.0–1.0).

    Splits on sentences/lines and weights each chunk by character length.
    HTML is stripped first. Returns 0.0 when there is too little text.
    """
    plain = strip_html(text)
    if len(plain) < _MIN_LANG_CHARS:
        return 0.0

    chunks = [
        chunk.strip()
        for chunk in _SENTENCE_SPLIT.split(plain)
        if len(chunk.strip()) >= _MIN_CHUNK_CHARS
    ]
    if not chunks:
        return 0.0

    detector = _get_language_detector()
    german_chars = 0
    total_chars = 0
    for chunk in chunks:
        language = detector.detect_language_of(chunk)
        total_chars += len(chunk)
        if language == Language.GERMAN:
            german_chars += len(chunk)
    if total_chars == 0:
        return 0.0
    return german_chars / total_chars


def detect_job_detail_language(text: str):
    """
    Detect job-detail language.

    Uses Lingua (Apache-2.0) n-gram models. Classified as German when more
    than GERMAN_SHARE_THRESHOLD of the text is German, so a few DE UI
    strings on an English Indeed page do not force "de".

    Callers that gate on language should use is_non_english_job_detail —
    only English JDs continue; other languages (DE, FR, …) are skipped and
    counted as german_filtered in the UI.

    Args:
        text: Job description HTML or plain text

    Returns:
        Tuple of (iso_code or None, german_share from 0.0 to 1.0).
        iso_code is a lowercase ISO 639-1 code such as 'de' or 'en'.
    """
    plain = strip_html(text)
    if len(plain) < _MIN_LANG_CHARS:
        return None, 0.0

    german_share = german_content_share(plain)
    if german_share > GERMAN_SHARE_THRESHOLD:
        return "de", german_share

    language = _get_language_detector().detect_language_of(plain)
    if language is None:
        return None, german_share
    return language.iso_code_639_1.name.lower(), german_share


def is_non_english_job_detail(text: str):
    """
    English-only gate for job detail pages.

    Returns (should_skip, lang, german_share). Skip when the JD is confidently
    not English (German, French, Dutch, …). Too-short / undetectable text does
    not skip (fail open). Callers still increment german_filtered — on DE job
    boards almost all non-English posts are German; FR etc. are rare but must
    not reach matching either.
    """
    lang, german_share = detect_job_detail_language(text)
    if lang is None or lang == "en":
        return False, lang, german_share
    return True, lang, german_share


def pause(min_seconds, max_seconds, message=None):
    """
    Sleep for a random interval to simulate human behavior.
    
    Args:
        min_seconds: Minimum delay in seconds
        max_seconds: Maximum delay in seconds
        message: Optional message to print with the delay time
    """
    delay = random.uniform(min_seconds, max_seconds)
    if message:
        print(f"{message} ({delay:.1f}s)")
    time.sleep(delay)


def safe_text(locator, timeout=3000):
    """
    Safely extract text content from a Playwright locator.
    Returns empty string if element not found or any error occurs.
    
    Args:
        locator: Playwright locator object
        timeout: Timeout in milliseconds
        
    Returns:
        Stripped text content or empty string
    """
    try:
        if locator.count() == 0:
            return ""
        return (locator.first.inner_text(timeout=timeout) or "").strip()
    except Exception:
        return ""


def safe_attr(locator, name, timeout=3000):
    """
    Safely extract an attribute value from a Playwright locator.
    Returns empty string if element not found or any error occurs.
    
    Args:
        locator: Playwright locator object
        name: Attribute name to extract
        timeout: Timeout in milliseconds
        
    Returns:
        Attribute value or empty string
    """
    try:
        if locator.count() == 0:
            return ""
        return locator.first.get_attribute(name, timeout=timeout) or ""
    except Exception:
        return ""


def parse_args():
    """
    Parse command-line arguments for the scraper.
    
    Returns:
        Parsed arguments with keywords and max_pages
    """
    parser = argparse.ArgumentParser(description="Scrape job listings into MongoDB.")
    parser.add_argument(
        "--keywords",
        "-k",
        nargs="+",
        default=DEFAULT_KEYWORDS,
        help='Search keywords. Example: -k frontend "full stack" "ai engineer"',
    )
    parser.add_argument(
        "--max-pages",
        "-p",
        type=int,
        default=None,
        help=(
            "How many result pages to scrape per keyword. "
            "Default: platform full budget; LinkedIn short windows "
            "(--hours < 24) use fewer pages."
        ),
    )
    parser.add_argument(
        "--max-jobs",
        "-j",
        type=int,
        default=None,
        help="Max job cards to process per page. Platform default if omitted.",
    )
    parser.add_argument(
        "--hours",
        type=float,
        default=None,
        help=(
            "LinkedIn only: posted-within window in hours "
            "(maps to f_TPR=r<seconds>). Default: config 24h."
        ),
    )
    return parser.parse_args()


def is_cdp_open():
    """
    Check if Chrome DevTools Protocol port is accessible.
    
    Returns:
        True if CDP port is open, False otherwise
    """
    try:
        with socket.create_connection((CDP_HOST, CDP_PORT), timeout=1):
            return True
    except OSError:
        return False


def start_debug_chrome(site_name="the website"):
    """
    Launch Chrome with remote debugging enabled.
    Uses a separate user data directory to avoid conflicts with regular Chrome.
    
    Args:
        site_name: Name of the website for error messages
        
    Raises:
        RuntimeError: If Chrome fails to start or CDP connection fails
    """
    print(
        f"Nothing is listening on {CDP_URL}. "
        "Starting Chrome with remote debugging..."
    )
    subprocess.Popen(
        [
            CHROME_BIN,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={CHROME_USER_DATA_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(20):
        if is_cdp_open():
            print("Chrome remote debugging is ready.")
            return
        time.sleep(0.5)
    raise RuntimeError(
        f"Could not connect to {CDP_URL}. Start Chrome yourself with:\n"
        f'  "{CHROME_BIN}" --remote-debugging-port={CDP_PORT} '
        f'--user-data-dir="{CHROME_USER_DATA_DIR}"\n'
        f"Open {site_name} in that window if needed, then run the scraper again."
    )


def open_scraper_page(context, bring_to_front=False):
    """
    Open a dedicated tab for one scraper.

    Parallel scrapers must not share context.pages[0]. A second page.goto on
    the same tab aborts the first navigation (net::ERR_ABORTED), so LinkedIn
    never finishes loading when Indeed starts at the same time.
    """
    page = context.new_page()
    if bring_to_front:
        try:
            page.bring_to_front()
        except Exception:
            pass
    return page


def is_target_closed_error(exc: BaseException) -> bool:
    """True when Playwright lost the tab/browser (TargetClosedError)."""
    name = type(exc).__name__
    if name == "TargetClosedError":
        return True
    msg = str(exc)
    return "Target page, context or browser has been closed" in msg


def goto_page(page, url, wait_until="domcontentloaded", attempts=3, timeout=60000):
    """
    Navigate, retrying when Chrome aborts the load.

    LinkedIn often replaces the original request with a redirect. Playwright
    reports that as net::ERR_ABORTED even if the destination later commits.
    """
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            page.goto(url, wait_until=wait_until, timeout=timeout)
            return
        except Exception as e:
            last_error = e
            aborted = "ERR_ABORTED" in str(e)
            if aborted:
                try:
                    current = page.url or ""
                except Exception:
                    current = ""
                if current and current != "about:blank" and "linkedin.com" in current:
                    print(f"Navigation aborted, continuing at {current}")
                    return
            if not aborted or attempt == attempts:
                raise
            print(f"Navigation aborted (attempt {attempt}/{attempts}), retrying...")
            time.sleep(2)
    raise last_error


def connect_browser(playwright, site_name="the website"):
    """
    Connect to Chrome via Chrome DevTools Protocol.
    Starts Chrome with debugging if not already running.
    
    Args:
        playwright: Playwright instance
        site_name: Name of the website for error messages
        
    Returns:
        Connected browser instance
        
    Raises:
        RuntimeError: If connection fails or no browser context found
    """
    if not is_cdp_open():
        start_debug_chrome(site_name)
    try:
        browser = playwright.chromium.connect_over_cdp(CDP_URL)
    except Exception as e:
        raise RuntimeError(
            f"Playwright could not attach to Chrome at {CDP_URL}: {e}\n"
            "If a normal Chrome is already open, this debug instance must use "
            f"--user-data-dir={CHROME_USER_DATA_DIR}."
        ) from e
    if not browser.contexts:
        raise RuntimeError("Chrome opened, but no browser context was found.")
    return browser
