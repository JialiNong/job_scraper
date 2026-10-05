import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
import os

from telegram import BotCommand, Update
from telegram.error import NetworkError, RetryAfter, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)
from telegram.request import HTTPXRequest

# Repo root: src/bot/telegram_bot.py -> ../../
PROJECT_DIR = Path(__file__).resolve().parents[2]
SRC_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_DIR / ".env")

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.challenge_wait import signal_indeed_resume  # noqa: E402
from core.pipeline_lock import pipeline_is_running  # noqa: E402
from core.match_digest import (  # noqa: E402
    build_match_messages,
    fetch_todays_matched_jobs,
)
from core.telegram_notify import telegram_send_kwargs  # noqa: E402

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = int(os.getenv("TELEGRAM_ALLOWED_USER_ID"))

# Prevent overlapping pipeline runs (full and quick share Chrome / scrapers)
_pipeline_running = False


def is_allowed(update: Update) -> bool:
    return (
        update.effective_user is not None
        and update.effective_user.id == ALLOWED_USER_ID
    )


def _httpx_request(*, read_timeout: float) -> HTTPXRequest:
    # Defaults are 5s; getUpdates long-poll and forum sends need more headroom.
    return HTTPXRequest(
        connect_timeout=15.0,
        read_timeout=read_timeout,
        write_timeout=15.0,
        pool_timeout=10.0,
    )


async def _safe_telegram(action, *, attempts: int = 4):
    """Retry TimedOut / RetryAfter / NetworkError instead of crashing the handler."""
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await action()
        except RetryAfter as exc:
            wait = float(getattr(exc, "retry_after", 5)) + 1.0
            print(f"Telegram flood wait {wait:.0f}s (attempt {attempt}/{attempts})")
            last_exc = exc
            await asyncio.sleep(wait)
        except (TimedOut, NetworkError) as exc:
            wait = 2.0 * attempt
            print(
                f"Telegram {type(exc).__name__}, retry in {wait:.0f}s "
                f"(attempt {attempt}/{attempts})"
            )
            last_exc = exc
            await asyncio.sleep(wait)
    print(f"Telegram send failed after {attempts} attempts: {last_exc}")
    return None


async def _reply(update: Update, text: str, **kwargs) -> None:
    if update.message is None:
        return
    await _safe_telegram(lambda: update.message.reply_text(text, **kwargs))


async def _send_push(bot, text: str, **kwargs) -> None:
    """Send to the configured group topic (not whatever chat the command came from)."""
    dest = telegram_send_kwargs()
    if dest.get("chat_id") is None:
        return
    await _safe_telegram(lambda: bot.send_message(**dest, text=text, **kwargs))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    await _reply(update, "👋 Job Assistant is online.")


async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    await _reply(update, "✅ Mac is connected and ready.")


async def _push_todays_matched_jobs(bot) -> None:
    """Send today's matched jobs as one combined card (split only if too long)."""
    try:
        jobs = await asyncio.to_thread(fetch_todays_matched_jobs)
    except Exception as exc:
        await _send_push(bot, f"⚠️ Failed to load matches: {exc}")
        return

    if not jobs:
        await _send_push(bot, "📭 No matched jobs for today.")
        return

    for message in build_match_messages(jobs):
        await _send_push(
            bot,
            message,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        await asyncio.sleep(0.4)


async def _run_pipeline(
    bot,
    script: str,
    done_text: str,
) -> None:
    """
    Run a scrape pipeline in the background; notify when done.

    Match cards are pushed by the shell script itself (run_task.sh /
    run_quick.sh) so cron / launchd / Telegram all get the same digest.
    The bot only sends Started / Done status here.
    """
    global _pipeline_running
    status_text = done_text
    try:
        process = await asyncio.create_subprocess_exec(
            "caffeinate",
            "-i",
            script,
            cwd=str(PROJECT_DIR),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        returncode = await process.wait()

        if returncode != 0:
            status_text = (
                f"❌ Pipeline exited with code {returncode}. Check logs/."
            )
    except Exception as exc:
        status_text = f"❌ Pipeline error: {exc}"
    finally:
        _pipeline_running = False

    # Notify separately — a Telegram flood/timeout is not a pipeline failure.
    await _send_push(bot, status_text)


async def _start_pipeline(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    script: str,
    started_text: str,
    done_text: str,
) -> None:
    global _pipeline_running

    if not is_allowed(update):
        return

    if _pipeline_running or pipeline_is_running():
        await _reply(
            update,
            "⏳ Pipeline is already running. I'll notify you when it finishes.",
        )
        return

    _pipeline_running = True

    # Start first so a slow/failed "Started" reply cannot skip the run
    # or leave the lock stuck without a task.
    context.application.create_task(
        _run_pipeline(
            context.bot,
            script,
            done_text,
        )
    )
    await _reply(update, started_text)


async def jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /jobs — full scrape + AI match (Indeed + LinkedIn, ~24h window).

    Flow:
      /jobs → "Started" → caffeinate -i ./run_task.sh
            → per-source scrape/match pings → match cards after both lanes
            → "Done"
    """
    await _start_pipeline(
        update,
        context,
        script="./run_task.sh",
        started_text=(
            "🚀 Started — Indeed and LinkedIn are scraping in parallel "
            "(~40–60 min). You'll get a ping when each source finishes "
            "scrape and match. Job cards arrive after both lanes are done."
        ),
        done_text="✅ Done — both platforms finished.",
    )


async def quick_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /quick_jobs — morning light scrape + AI match.

    Indeed reuses the 24h search with fewer pages per keyword.
    LinkedIn uses the 12h quick URL (no keyword loop).

    Flow:
      /quick_jobs → "Started" → caffeinate -i ./run_quick.sh
                  → per-source scrape/match pings → match cards after both
                    lanes → "Done"
    """
    await _start_pipeline(
        update,
        context,
        script="./run_quick.sh",
        started_text=(
            "⚡ Started — light scrape, Indeed and LinkedIn in parallel "
            "(~20–40 min). You'll get a ping when each source finishes "
            "scrape and match. Job cards arrive after both lanes are done."
        ),
        done_text="✅ Done — both platforms finished.",
    )


async def matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/matches — push today's matched job cards without running a scrape."""
    if not is_allowed(update):
        return

    await _reply(update, "📥 Fetching today's matches...")
    await _push_todays_matched_jobs(context.bot)


async def indeed_ok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /indeed_ok — tell a paused Indeed scraper that human verification is done.

    The scraper polls logs/indeed_human_resume.flag; this command writes it.
    """
    if not is_allowed(update):
        return

    path = signal_indeed_resume()
    await _reply(
        update,
        "✅ Resume signal sent. The Indeed scraper will continue shortly.\n"
        f"({path.name})",
    )


async def indeed_resume_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Inline button: Continue after Indeed human verification."""
    query = update.callback_query
    if query is None:
        return

    if not is_allowed(update):
        await query.answer("Not authorized.", show_alert=True)
        return

    if query.data != "indeed_resume":
        await query.answer()
        return

    path = signal_indeed_resume()
    await query.answer("Resume signal sent")
    try:
        await query.edit_message_text(
            "✅ Resume signal sent. The Indeed scraper will continue shortly.\n"
            f"({path.name})"
        )
    except Exception:
        # Message may already be edited or too old — still confirm in chat
        await _send_push(
            context.bot,
            "✅ Resume signal sent. The Indeed scraper will continue shortly.",
        )


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    print(f"Telegram handler error: {type(err).__name__}: {err}")


async def _post_init(app: Application) -> None:
    """Register slash commands so they appear in Telegram's menu."""
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Check bot is online"),
            BotCommand("test", "Check Mac is ready"),
            BotCommand("jobs", "Full scrape + AI match (~40–60 min)"),
            BotCommand("quick_jobs", "Light scrape + AI match (~20–40 min)"),
            BotCommand("matches", "Push today's matched job cards"),
            BotCommand("indeed_ok", "Resume Indeed after human verification"),
        ]
    )


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(_httpx_request(read_timeout=30.0))
        .get_updates_request(_httpx_request(read_timeout=40.0))
        .post_init(_post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("test", test))
    app.add_handler(CommandHandler("jobs", jobs))
    app.add_handler(CommandHandler("quick_jobs", quick_jobs))
    app.add_handler(CommandHandler("matches", matches))
    app.add_handler(CommandHandler("indeed_ok", indeed_ok))
    app.add_handler(CallbackQueryHandler(indeed_resume_callback, pattern=r"^indeed_resume$"))
    app.add_error_handler(_on_error)

    print(f"Telegram bot is running... (cwd={PROJECT_DIR})")
    print("Commands: /start /test /jobs /quick_jobs /matches /indeed_ok")

    # Drop queued commands from the flood so restart does not replay /jobs.
    app.run_polling(drop_pending_updates=True, timeout=20)


if __name__ == "__main__":
    main()
