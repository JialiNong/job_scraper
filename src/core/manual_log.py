"""
Log Job save path that must run on Render (no Playwright / no scrapers).

Save Link Only and scrape-fallback stubs live here so importing this module
never loads playwright, trafilatura, Chrome helpers, or ai_matcher.
"""
from __future__ import annotations

from datetime import datetime

from core.db_mongo import (
    APPLIED_STATUSES,
    get_collection,
    indeed_job_id_variants,
    is_job_on_tracker,
)
from core.job_url import (
    detect_source,
    job_stub_from_url,
    link_only_job_title,
    normalize_pasted_job_url,
    plan_manual_log_action,
    resolve_job_id,
)


def stub_ai_pending_analysis() -> dict:
    """Placeholder analysis for link-only / scrape-failed saves."""
    return {
        "match_score": None,
        "recommendation": "",
        "special_match": False,
        "special_match_reasons": [],
        "disqualification_reason": "",
        "match_reasons": ["Link-only save — AI pending"],
        "missing_requirements": [],
        "red_flags": [],
        "nice_to_have_matches": [],
        "summary": "Link saved without scrape/AI. Use Run AI on Tracker when Chrome is available.",
        "what_youll_do": {"matched": [], "unmatched": []},
        "what_theyre_looking_for": {"matched": [], "unmatched": []},
        "match_gate": "",
        "requirement_extract": {},
    }


def analysis_fields(analysis: dict) -> dict:
    """Normalize analysis into the fields stored on job documents (no AI import)."""
    analysis = analysis or {}
    reasons = analysis.get("special_match_reasons") or []
    if not isinstance(reasons, list):
        reasons = [str(reasons)] if reasons else []

    def _pair(value):
        if not isinstance(value, dict):
            return {"matched": [], "unmatched": []}
        return {
            "matched": list(value.get("matched") or []),
            "unmatched": list(value.get("unmatched") or []),
        }

    return {
        "recommendation": analysis.get("recommendation", ""),
        "special_match": bool(analysis.get("special_match")),
        "special_match_reasons": [str(item).strip() for item in reasons if str(item).strip()],
        "disqualification_reason": analysis.get("disqualification_reason", ""),
        "match_reasons": analysis.get("match_reasons", []),
        "missing_requirements": analysis.get("missing_requirements", []),
        "red_flags": analysis.get("red_flags", []),
        "nice_to_have_matches": analysis.get("nice_to_have_matches", []),
        "summary": analysis.get("summary", ""),
        "what_youll_do": _pair(analysis.get("what_youll_do")),
        "what_theyre_looking_for": _pair(analysis.get("what_theyre_looking_for")),
        "match_gate": analysis.get("match_gate") or "",
        "requirement_extract": analysis.get("requirement_extract") or {},
    }


def mark_tracker_job_applied(job_id: str, source: str, applied_date: datetime = None) -> str:
    """
    Set an existing Tracker row to applied without wiping notes / timeline.

    Returns:
        applied  — status updated (or applied_at stamped)
        already  — already an application record
        missing  — not on Tracker
    """
    col = get_collection("matched_jobs")
    if source == "indeed":
        variants = indeed_job_id_variants(job_id)
        query = {"job_id": {"$in": variants} if variants else job_id, "source": source}
    else:
        query = {"job_id": job_id, "source": source}
    job = col.find_one(query)
    if not job:
        return "missing"

    now = datetime.now()
    t = applied_date or now
    already_applied = bool(job.get("applied_at")) or job.get("status") in APPLIED_STATUSES
    if already_applied and (job.get("status") or "") in APPLIED_STATUSES:
        return "already"

    fields = {
        "status": "applied",
        "updated_at": now,
        "manually_applied": True,
        "manually_logged": True,
    }
    if not job.get("applied_at"):
        fields["applied_at"] = t
    col.update_one({"_id": job["_id"]}, {"$set": fields})
    return "applied"


def _print_saved(status: str, applied_date: datetime = None, *, extra: str = ""):
    suffix = extra if extra.startswith(",") or not extra else f", {extra}"
    if status == "applied":
        date_str = applied_date.strftime("%Y-%m-%d") if applied_date else "today"
        print(f"  ✅ Saved to matched_jobs (status=applied, applied_at={date_str}{suffix})")
    else:
        print(f"  ✅ Saved to matched_jobs (status=pending / Not Applied{suffix})")


def force_save_manual_job(
    job: dict,
    analysis: dict,
    status: str = "pending",
    applied_date: datetime = None,
    *,
    ai_pending: bool = False,
) -> bool:
    """
    Save to matched_jobs regardless of AI score.
    If the job already exists in matched_jobs, update it instead of inserting.
    Also upsert the record in the main jobs collection.
    """
    if status not in ("pending", "applied"):
        status = "pending"

    jobs_col = get_collection("jobs")
    matched_col = get_collection("matched_jobs")

    now = datetime.utcnow()
    t = (applied_date or now) if status == "applied" else now
    job_id = job["job_id"]
    source = job["source"]
    match_score = analysis["match_score"] if "match_score" in analysis else 0

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
        "match_score": match_score,
        "ai_pending":  bool(ai_pending),
        **analysis_fields(analysis),
    }
    if status == "applied":
        jobs_set["applied_at"] = t

    jobs_col.update_one(
        {"job_id": job_id, "source": source},
        {
            "$setOnInsert": {"created_at": t, "link": job.get("link", "")},
            "$set": jobs_set,
        },
        upsert=True,
    )

    matched_data = {
        "title":       job.get("title", ""),
        "company":     job.get("company", ""),
        "location":    job.get("location", ""),
        "link":        job.get("link", ""),
        "job_id":      job_id,
        "source":      source,
        "description": job.get("description", ""),
        "applicants":  job.get("applicants", ""),
        "match_score": match_score,
        **analysis_fields(analysis),
        "status":      status,
        "matched_at":  t,
        "updated_at":  t,
        "notes":       "",
        "application_qa": [],
        "manually_logged": True,
        "ai_pending":  bool(ai_pending),
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


force_save_applied_job = force_save_manual_job


def handle_existing_tracker(url: str, job_id: str, source: str, status: str, applied_date) -> str:
    """skip | applied | already | missing — print a line the UI can count."""
    action = plan_manual_log_action(True, status, job_id)
    if action == "mark_applied":
        result = mark_tracker_job_applied(job_id, source, applied_date)
        if result == "applied":
            date_str = applied_date.strftime("%Y-%m-%d") if applied_date else "today"
            print(
                f"  ✅ Marked existing Tracker job as applied "
                f"(job_id={job_id}, applied_at={date_str})"
            )
            return "applied"
        if result == "already":
            print(
                f"  ⚠️  Already on Tracker as an application: "
                f"job_id={job_id} source={source} — skipping"
            )
            return "already"
        print(f"  ⚠️  Tracker row vanished for job_id={job_id} — will save a new card")
        return "missing"
    print(
        f"  ⚠️  Already on Tracker (matched_jobs): "
        f"job_id={job_id} source={source} — skipping"
    )
    return "skip"


def save_job_record(
    job: dict,
    analysis: dict,
    status: str,
    applied_date,
    *,
    ai_pending: bool,
    extra: str = "",
) -> bool:
    if not job.get("title"):
        job["title"] = link_only_job_title(
            job.get("link") or "",
            job.get("source") or "",
            job.get("job_id") or "",
        )
    ok = force_save_manual_job(
        job,
        analysis,
        status=status,
        applied_date=applied_date,
        ai_pending=ai_pending,
    )
    if not ok:
        return False
    note = extra
    if ai_pending and "AI pending" not in extra:
        note = (extra + ", AI pending").strip(", ")
    _print_saved(status, applied_date, extra=note)
    return True


def save_link_only_urls(
    urls: list[str],
    applied_date: datetime = None,
    status: str = "pending",
):
    """
    Save job URLs to the Tracker without opening a browser or running AI.

    Safe on Render: no Playwright, Chrome, or AI imports.
    """
    if status not in ("pending", "applied"):
        status = "pending"

    if not urls:
        print("No URLs provided.")
        return

    status_label = "applied" if status == "applied" else "pending (Not Applied)"
    print(f"\n🔗 Link-only save — {len(urls)} URL(s) as {status_label} (no scrape / no AI)…")

    results = {"ok": [], "failed": [], "skipped": []}
    analysis = stub_ai_pending_analysis()

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
            outcome = handle_existing_tracker(url, job_id, source, status, applied_date)
            if outcome == "applied":
                results["ok"].append(url)
            elif outcome in ("already", "skip"):
                results["skipped"].append(url)
            if outcome != "missing":
                continue

        job = job_stub_from_url(url, scrape_warning="Link-only save")
        if not job:
            print("  ❌ Cannot build a job record from this URL")
            results["failed"].append(url)
            continue
        ok = save_job_record(
            job, analysis, status, applied_date, ai_pending=True, extra="link-only"
        )
        if not ok:
            results["failed"].append(url)
            continue
        results["ok"].append(url)

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
