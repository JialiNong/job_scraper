"""
Indeed human-verification (challenge / captcha) detection and resume handshake.

Flow:
  1. Scraper detects a challenge page → clears resume flag → Telegram notify
  2. User solves the challenge in the debug Chrome window
  3. User taps Continue or sends /indeed_ok (bot writes the resume flag)
  4. Scraper sees the flag, clears it, and continues

Manual fallback (bot offline):
  touch logs/indeed_human_resume.flag
"""
from __future__ import annotations

import time
from pathlib import Path

from core.telegram_notify import indeed_challenge_keyboard, send_telegram_message

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LOGS_DIR = _PROJECT_ROOT / "logs"
RESUME_FLAG_PATH = _LOGS_DIR / "indeed_human_resume.flag"

# How long the scraper waits for the user after notifying Telegram
DEFAULT_RESUME_TIMEOUT_SEC = 45 * 60
POLL_INTERVAL_SEC = 2.0

# URL / DOM / text hints for Indeed (and common anti-bot) challenge pages
_CHALLENGE_URL_HINTS = (
    "challenge",
    "blocked",
    "security-check",
    "security/check",
    "account/verify",
    "authwall",
    "captcha",
    "cdn-cgi/challenge",
)

_CHALLENGE_SELECTORS = (
    "#px-captcha",
    "#challenge-form",
    "[data-testid='challenge-stage']",
    "iframe[src*='captcha']",
    "iframe[src*='challenge']",
    "iframe[title*='challenge' i]",
    ".g-recaptcha",
    "#recaptcha",
)

_CHALLENGE_TEXT_HINTS = (
    "verify you are a human",
    "confirm you are a human",
    "press and hold",
    "presse und halte",
    "are you a robot",
    "unusual traffic",
    "additional verification",
    "security check",
    "with a tap hold",
    "please verify you are a human",
    "bitte bestätige, dass du ein mensch bist",
    "bitte bestätigen sie, dass sie ein mensch sind",
)


def clear_resume_flag() -> None:
    """Remove a stale resume signal so we do not auto-continue from a prior run."""
    try:
        RESUME_FLAG_PATH.unlink(missing_ok=True)
    except OSError as exc:
        print(f"⚠ Could not clear resume flag: {exc}")


def signal_indeed_resume() -> Path:
    """
    Write the resume flag (called by the Telegram bot after user confirms).

    Returns the flag path.
    """
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    RESUME_FLAG_PATH.write_text(f"resume_at={time.time()}\n", encoding="utf-8")
    return RESUME_FLAG_PATH


def resume_flag_exists() -> bool:
    return RESUME_FLAG_PATH.is_file()


def is_indeed_challenge_page(page) -> bool:
    """
    Best-effort detection of an Indeed human-verification / captcha page.

    Prefer URL + dedicated widgets; fall back to visible challenge copy.
    """
    try:
        url = (page.url or "").lower()
    except Exception:
        url = ""

    if any(hint in url for hint in _CHALLENGE_URL_HINTS):
        return True

    for selector in _CHALLENGE_SELECTORS:
        try:
            loc = page.locator(selector)
            if loc.count() > 0:
                first = loc.first
                try:
                    if first.is_visible():
                        return True
                except Exception:
                    return True
        except Exception:
            continue

    # Sample a limited slice of page text to avoid huge dumps
    try:
        body = page.locator("body")
        if body.count() == 0:
            return False
        text = (body.first.inner_text(timeout=3000) or "")[:4000].lower()
    except Exception:
        return False

    return any(hint in text for hint in _CHALLENGE_TEXT_HINTS)


def _challenge_looks_cleared(page, ready_selector: str = "") -> bool:
    """True when the challenge UI is gone and (optionally) job cards are back."""
    try:
        if page.is_closed():
            return False
    except Exception:
        return False
    try:
        if is_indeed_challenge_page(page):
            return False
    except Exception:
        return False
    if not ready_selector:
        return True
    try:
        return page.locator(ready_selector).count() > 0
    except Exception:
        return False


def wait_for_indeed_human_resume(
    *,
    reason: str = "Indeed human verification required",
    timeout_sec: float = DEFAULT_RESUME_TIMEOUT_SEC,
    page=None,
    ready_selector: str = "",
) -> bool:
    """
    Notify via Telegram and block until resume is safe.

    Resume when any of these happen:
      - user taps Continue / sends /indeed_ok (resume flag)
      - the challenge UI is gone and the SERP looks ready again
      - timeout (returns False)
    """
    clear_resume_flag()
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)

    message = (
        "⚠️ Indeed needs human verification\n\n"
        f"{reason}\n\n"
        "1. Switch to the debug Chrome window and complete the check\n"
        "2. When the job list is back, the scraper continues on its own\n"
        "   (or tap Continue / send /indeed_ok)\n\n"
        f"Fallback (bot offline): touch {RESUME_FLAG_PATH}"
    )
    sent = send_telegram_message(
        message,
        reply_markup=indeed_challenge_keyboard(),
    )
    if sent:
        print(
            "Telegram alert sent — waiting for the job list to return, "
            "or /indeed_ok / Continue…"
        )
    else:
        print(
            f"Telegram alert failed — waiting for resume flag: "
            f"touch {RESUME_FLAG_PATH}"
        )

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if resume_flag_exists():
            clear_resume_flag()
            print("✅ Resume signal received — continuing Indeed scrape")
            time.sleep(2.0)
            return True
        if page is not None and _challenge_looks_cleared(page, ready_selector):
            print("✅ Indeed challenge looks cleared — continuing without a tap")
            time.sleep(1.5)
            return True
        time.sleep(POLL_INTERVAL_SEC)

    print(
        f"❌ Timed out after {timeout_sec / 60:.0f} min waiting for Indeed "
        "human verification resume"
    )
    return False


def handle_indeed_challenge_if_needed(
    page,
    *,
    context: str = "",
    ready_selector: str = "",
) -> bool:
    """
    If the current page looks like a challenge, wait for the user then return.

    Returns True when a challenge was detected (whether or not resume succeeded).
    Returns False when no challenge was found.
    Callers should re-check readiness after a True return + successful resume.

    Does not solve the challenge. It only waits until you finish it in Chrome
    (auto-continue) or confirm via Telegram.
    """
    if not is_indeed_challenge_page(page):
        return False

    reason = "Indeed challenge / captcha page detected"
    if context:
        reason = f"{reason} ({context})"
    print(f"\n🚨 {reason}")
    try:
        print(f"   URL: {page.url}")
    except Exception:
        pass

    resumed = wait_for_indeed_human_resume(
        reason=reason,
        page=page,
        ready_selector=ready_selector,
    )
    if not resumed:
        raise RuntimeError(
            "Indeed human verification was not confirmed in time; aborting scrape"
        )
    return True
