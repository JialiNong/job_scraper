"""
Lightweight Telegram Bot API helper for scrapers (sync, no PTB dependency).

Destination (pushes / alerts):
  TELEGRAM_CHAT_ID + optional TELEGRAM_MESSAGE_THREAD_ID (forum topic).
  Falls back to TELEGRAM_ALLOWED_USER_ID when CHAT_ID is unset (DM mode).

Auth (who may run bot commands) stays on TELEGRAM_ALLOWED_USER_ID.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")


def _bot_token() -> str:
    return (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()


def _parse_optional_int(raw: Optional[str]) -> Optional[int]:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def telegram_chat_id() -> Optional[int]:
    """Group/supergroup (or DM) that should receive job pushes."""
    return _parse_optional_int(
        os.getenv("TELEGRAM_CHAT_ID")
    ) or _parse_optional_int(os.getenv("TELEGRAM_ALLOWED_USER_ID"))


def telegram_message_thread_id() -> Optional[int]:
    """Forum topic id; None means no thread (DM or General)."""
    return _parse_optional_int(os.getenv("TELEGRAM_MESSAGE_THREAD_ID"))


def telegram_send_kwargs() -> dict[str, Any]:
    """chat_id / message_thread_id kwargs for Bot API or PTB send_message."""
    kwargs: dict[str, Any] = {"chat_id": telegram_chat_id()}
    thread_id = telegram_message_thread_id()
    if thread_id is not None:
        kwargs["message_thread_id"] = thread_id
    return kwargs


def send_telegram_message(
    text: str,
    *,
    reply_markup: Optional[dict[str, Any]] = None,
    parse_mode: Optional[str] = None,
    disable_web_page_preview: bool = True,
) -> bool:
    """
    Send a message to the configured chat / topic via Telegram Bot API.

    Returns True on HTTP success. Failures are logged and return False
    (scrapers should keep working without Telegram).
    """
    token = _bot_token()
    dest = telegram_send_kwargs()
    if not token or dest.get("chat_id") is None:
        print(
            "⚠ Telegram notify skipped: TELEGRAM_BOT_TOKEN / "
            "TELEGRAM_CHAT_ID (or TELEGRAM_ALLOWED_USER_ID) missing"
        )
        return False

    payload: dict[str, Any] = {
        **dest,
        "text": text,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps(payload).encode("utf-8")

    last_error = ""
    for attempt in range(1, 4):
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.loads(response.read().decode("utf-8") or "{}")
            if body.get("ok"):
                return True
            retry_after = (body.get("parameters") or {}).get("retry_after")
            last_error = str(body)
            if retry_after is not None and attempt < 3:
                wait = float(retry_after) + 1.0
                print(f"⚠ Telegram flood wait {wait:.0f}s (attempt {attempt}/3)")
                time.sleep(wait)
                continue
            print(f"⚠ Telegram notify rejected: {body}")
            return False
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code == 429 and attempt < 3:
                wait = 6.0 * attempt
                print(f"⚠ Telegram HTTP 429, retry in {wait:.0f}s (attempt {attempt}/3)")
                time.sleep(wait)
                continue
            print(f"⚠ Telegram notify failed: {last_error}")
            return False
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < 3:
                wait = 2.0 * attempt
                print(f"⚠ Telegram {last_error}, retry in {wait:.0f}s (attempt {attempt}/3)")
                time.sleep(wait)
                continue
            print(f"⚠ Telegram notify failed: {last_error}")
            return False

    print(f"⚠ Telegram notify failed: {last_error}")
    return False


def indeed_challenge_keyboard() -> dict[str, Any]:
    """Inline keyboard: Continue button → callback_data indeed_resume."""
    return {
        "inline_keyboard": [
            [{"text": "✅ Continue", "callback_data": "indeed_resume"}]
        ]
    }
