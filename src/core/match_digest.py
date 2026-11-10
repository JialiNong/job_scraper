"""
Build and send today's matched-job digest to Telegram.

Used by:
  - scripts/run_jobs.sh (push after both lanes finish)
"""
from __future__ import annotations

import html
import time
from datetime import datetime
from core.db_mongo import get_collection
from core.telegram_notify import send_telegram_message

# Telegram hard limit is 4096; stay under to leave room for UTF-16 emoji expansion
_TELEGRAM_MSG_LIMIT = 3500

STATUS_LABELS = {
    "pending": "Not Applied",
    "applied": "Applied",
    "rejected": "Rejected",
    "interview": "Interview",
    "offer": "Offer",
    "unsuitable": "Unsuitable",
    "closed": "Closed",
    "closed_no_reply": "Closed · No Reply",
    "prev_rejected": "Prev. Rejected",
    "too_many_questions": "Skipped · Long Form",
    "repost": "Repost",
    "duplicate": "Duplicate",
}


# Display names for the API sources (src/sources/). LinkedIn / Indeed cards keep
# their original format: .title() label, no company line.
SOURCE_LABELS = {
    "arbeitnow": "Arbeitnow",
    "arbeitsagentur": "Bundesagentur",
    "adzuna": "Adzuna",
    "ats": "Company site",
}


def normalize_job_link(job: dict) -> str:
    """Return an absolute job URL (LinkedIn/Indeed relative hrefs → https)."""
    link = (job.get("link") or "").strip()
    source = (job.get("source") or "").lower()
    job_id = str(job.get("job_id") or "").strip()

    if source == "linkedin":
        if job_id:
            return f"https://www.linkedin.com/jobs/view/{job_id}/"
        if link and not link.startswith("http"):
            return "https://www.linkedin.com" + link

    if link and not link.startswith("http") and source == "indeed":
        return "https://www.indeed.com" + link

    return link


def fetch_todays_matched_jobs(run_id: str | None = None) -> list[dict]:
    """
    Return matched_jobs, highest score first.

    With run_id: only rows the pipeline run with that id wrote (Telegram /jobs).
    Without: every row matched today (manual logs and agents included).
    """
    matched_jobs = get_collection("matched_jobs")
    if run_id:
        query = {"run_id": run_id}
    else:
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        query = {"matched_at": {"$gte": today_start}}
    return list(
        matched_jobs.find(
            query,
            {
                "title": 1,
                "location": 1,
                "match_score": 1,
                "source": 1,
                "link": 1,
                "job_id": 1,
                "company": 1,
                "status": 1,
                "special_match": 1,
            },
        ).sort("match_score", -1)
    )


def format_job_block(index: int, job: dict) -> str:
    """One job block inside the combined daily card (Telegram HTML)."""
    title = html.escape(str(job.get("title") or "Untitled"))
    location = html.escape(str(job.get("location") or "N/A"))
    source_key = str(job.get("source") or "unknown").lower()
    source = html.escape(SOURCE_LABELS.get(source_key, source_key.title()))
    company = html.escape(str(job.get("company") or "")) if source_key in SOURCE_LABELS else ""
    status_key = str(job.get("status") or "pending")
    status = html.escape(STATUS_LABELS.get(status_key, status_key))

    score = job.get("match_score", 0)
    try:
        score_text = f"{float(score):.1f}"
    except (TypeError, ValueError):
        score_text = str(score)

    lines = [
        f"<b>{index}. {title}</b>",
        *([f"🏢 {company}"] if company else []),
        f"📍 {location}",
        f"⭐ {score_text}/10 · {source}",
        f"🏷 {status}",
    ]
    if job.get("special_match"):
        lines.insert(-1, "★ Special Match")

    link = normalize_job_link(job)
    if link.startswith("http"):
        lines.append(f'🔗 <a href="{html.escape(link, quote=True)}">Open job</a>')

    return "\n".join(lines)


def build_match_messages(jobs: list[dict], label: str = "Today's matches") -> list[str]:
    """Pack all jobs into as few Telegram messages as possible."""
    header = f"📋 {label}: <b>{len(jobs)}</b>\n"
    blocks = [format_job_block(i, job) for i, job in enumerate(jobs, start=1)]

    messages: list[str] = []
    current = header

    for block in blocks:
        candidate = current + "\n\n" + block if current != header else header + "\n" + block
        if len(candidate) <= _TELEGRAM_MSG_LIMIT:
            current = candidate
            continue

        if current != header:
            messages.append(current)
        if len(header + "\n" + block) > _TELEGRAM_MSG_LIMIT:
            messages.append((header + "\n" + block)[:_TELEGRAM_MSG_LIMIT])
            current = header
        else:
            current = header + "\n" + block

    if current != header:
        messages.append(current)

    return messages


def push_todays_matched_jobs(run_id: str | None = None) -> int:
    """
    Send matched jobs via the Bot API (sync).

    With run_id, only that pipeline run's new matches are sent; otherwise all of today's.
    Returns the number of jobs included (0 if none / notify skipped).
    """
    try:
        jobs = fetch_todays_matched_jobs(run_id)
    except Exception as exc:
        send_telegram_message(f"⚠️ Failed to load matches: {exc}")
        return 0

    if not jobs:
        send_telegram_message(
            "📭 No new matches from this run." if run_id else "📭 No matched jobs for today."
        )
        return 0

    label = "This run's matches" if run_id else "Today's matches"
    sent = 0
    for message in build_match_messages(jobs, label=label):
        if send_telegram_message(
            message,
            parse_mode="HTML",
            disable_web_page_preview=True,
        ):
            sent += 1
        time.sleep(0.25)

    print(f"Telegram match digest: {len(jobs)} jobs in {sent} message(s)")
    return len(jobs)


if __name__ == "__main__":
    # CLI: python3 -m core.match_digest   (from src/) or via scripts
    n = push_todays_matched_jobs()
    raise SystemExit(0 if n >= 0 else 1)
