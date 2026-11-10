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

# Repo root: src/tg/bot.py -> ../../
PROJECT_DIR = Path(__file__).resolve().parents[2]
SRC_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_DIR / ".env")

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.challenge_wait import signal_indeed_resume  # noqa: E402
from core.config import INDEED_CONFIG  # noqa: E402
from core.pipeline_lock import pipeline_is_running  # noqa: E402
from tg.client import telegram_send_kwargs  # noqa: E402

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = int(os.getenv("TELEGRAM_ALLOWED_USER_ID"))

PIPELINE_SCRIPT = "./scripts/run_jobs.sh"
INDEED_DAY_CHOICES = INDEED_CONFIG["fromage_choices"]

# Prevent overlapping pipeline runs (they share one debug Chrome)
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


async def _run_pipeline(
    bot,
    script: str,
    done_text: str,
    script_args: list[str] | None = None,
) -> None:
    """
    Run a scrape pipeline in the background; notify when done.

    Match cards are pushed by scripts/run_jobs.sh itself after both lanes
    finish. The bot only sends Started / Done status here.
    """
    global _pipeline_running
    status_text = done_text
    try:
        cmd = ["caffeinate", "-i", script, *(script_args or [])]
        process = await asyncio.create_subprocess_exec(
            *cmd,
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
    script_args: list[str] | None = None,
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
            script_args=script_args,
        )
    )
    await _reply(update, started_text)


JOBS_USAGE = (
    "Usage: /jobs [days] [linkedin_hours] [pages]\n"
    f"days → Indeed window, one of: {', '.join(str(d) for d in INDEED_DAY_CHOICES)}\n"
    "linkedin_hours → LinkedIn window (default: days × 24)\n"
    "pages → pages per keyword on both platforms (default from config)\n"
    "Examples: /jobs · /jobs 7 · /jobs 3 120 · /jobs 3 72 2"
)


def _parse_jobs_args(
    args: list[str] | None,
) -> tuple[int | None, float | None, int | None, str | None]:
    """
    Parse optional /jobs [days] [linkedin_hours] [pages].

    Returns (days, hours, pages, error_message). All None → script defaults
    (both platforms last 24h, config page budget).
    """
    if not args:
        return None, None, None, None
    if len(args) > 3:
        return None, None, None, JOBS_USAGE

    try:
        days = int(args[0].strip())
    except ValueError:
        return None, None, None, JOBS_USAGE
    if days not in INDEED_DAY_CHOICES:
        return None, None, None, JOBS_USAGE

    hours: float | None = None
    if len(args) >= 2:
        try:
            hours = float(args[1].strip())
        except ValueError:
            return None, None, None, JOBS_USAGE
        if hours <= 0:
            return None, None, None, JOBS_USAGE

    pages: int | None = None
    if len(args) == 3:
        try:
            pages = int(args[2].strip())
        except ValueError:
            return None, None, None, JOBS_USAGE
        if pages <= 0:
            return None, None, None, JOBS_USAGE

    return days, hours, pages, None


async def jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /jobs [days] [linkedin_hours] [pages] — Indeed + LinkedIn in parallel,
    each followed by AI matching.

      /jobs           → both last 24h, config page budget
      /jobs 7         → Indeed 7 days, LinkedIn 168h
      /jobs 3 120     → Indeed 3 days, LinkedIn 120h
      /jobs 3 72 2    → Indeed 3 days, LinkedIn 72h, 2 pages per keyword

    Flow:
      /jobs → "Started" → caffeinate -i ./scripts/run_jobs.sh [--days N] [--hours H] [--pages P]
            → per-source scrape/match pings → match cards after both lanes
            → "Done"
    """
    days, hours, pages, err = _parse_jobs_args(context.args)
    if err:
        await _reply(update, err)
        return

    script_args: list[str] = []
    if days is None:
        window = "24h (default)"
    else:
        script_args += ["--days", str(days)]
        linkedin_hours = hours if hours is not None else days * 24
        if hours is not None:
            script_args += ["--hours", f"{hours:g}"]
        window = f"Indeed {days}d, LinkedIn {linkedin_hours:g}h"
    if pages is not None:
        script_args += ["--pages", str(pages)]
        window += f", {pages} page" + ("" if pages == 1 else "s") + "/keyword"

    await _start_pipeline(
        update,
        context,
        script=PIPELINE_SCRIPT,
        script_args=script_args,
        started_text=(
            f"🚀 Started — Indeed and LinkedIn are scraping in parallel, "
            f"{window}. You'll get a ping when each source finishes "
            "scrape and match. Job cards arrive after both lanes are done."
        ),
        done_text=f"✅ Done — both platforms finished ({window}).",
    )


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
            BotCommand("jobs", "Indeed + LinkedIn + match (/jobs [days] [li_hours] [pages])"),
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
    app.add_handler(CommandHandler("indeed_ok", indeed_ok))
    app.add_handler(CallbackQueryHandler(indeed_resume_callback, pattern=r"^indeed_resume$"))
    app.add_error_handler(_on_error)

    print(f"Telegram bot is running... (cwd={PROJECT_DIR})")
    print("Commands: /start /test /jobs /indeed_ok")

    # Drop queued commands from the flood so restart does not replay /jobs.
    app.run_polling(drop_pending_updates=True, timeout=20)


if __name__ == "__main__":
    main()
