"""Summary bar, funnel, heatmap, flow charts, locations, and Application Journey."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_login import login_required

from core.db_mongo import (
    APPLICATION_QUERY,
    count_applied_applications,
    count_open_timeout_jobs,
    count_unmatched_jobs,
    get_applied_location_stats,
    get_collection,
    get_daily_activity_stats,
    get_scraper_stats,
    reapplied_count,
    resolve_applied_at,
)
from web.flow import build_sankey, build_weekly_by_platform
from web.status import (
    GOT_INTERVIEW_FILTER,
    JOURNEY_INTERVIEW_STEPS,
    JOURNEY_TRACK_STEPS,
    MATCH_THRESHOLD,
    TIMELINE_STEPS,
    _format_timeline_at,
    _parse_timeline_at,
    chip_status_counts,
)

bp = Blueprint("stats", __name__)

# Fields the Journey loop reads (incl. resolve_applied_at / reapplied_count).
# Without a projection every applied job's full document (description, AI
# breakdowns) came back from Atlas: ~2.6 MB for ~320 jobs vs ~80 KB.
JOURNEY_PROJECTION = {
    "company": 1,
    "title": 1,
    "status": 1,
    "application_timeline": 1,
    "applied_at": 1,
    "updated_at": 1,
    "matched_at": 1,
}

# Sankey + weekly bars: timeline / status / apply date / source. Same
# APPLICATION_QUERY set as Journey; no JD or AI breakdowns.
FLOW_PROJECTION = {
    "status": 1,
    "source": 1,
    "application_timeline": 1,
    "applied_at": 1,
    "updated_at": 1,
    "matched_at": 1,
}


@bp.route("/api/stats")
@login_required
def api_stats():
    """Return status counts for the summary bar and pipeline funnel."""
    collection = get_collection("matched_jobs")

    def chip_docs():
        # Slim projection: progress chips use timeline / applied_at, not status alone
        return list(collection.find(
            {},
            {"_id": 0, "status": 1, "applied_at": 1, "application_timeline.step": 1},
        ))

    # The five reads are independent and each costs a round trip to Atlas:
    # run them side by side (pymongo's client is thread-safe) instead of in a row.
    with ThreadPoolExecutor(max_workers=5) as pool:
        docs_f = pool.submit(chip_docs)
        # Same as Journey + Locations: APPLICATION_QUERY (keeps side-status applies)
        applied_f = pool.submit(count_applied_applications)
        scraper_f = pool.submit(get_scraper_stats)
        unmatched_f = pool.submit(count_unmatched_jobs, threshold=MATCH_THRESHOLD)
        timeouts_f = pool.submit(count_open_timeout_jobs)
        docs = docs_f.result()
        applied_total = applied_f.result()
        scraper = scraper_f.result()
        unmatched = unmatched_f.result()
        timeouts_open = timeouts_f.result()

    by_status = chip_status_counts(docs)
    ai_matched = len(docs)

    return jsonify({
        # existing: matched-jobs filter chips
        "total":     ai_matched,
        "by_status": by_status,
        "unmatched": unmatched,
        "timeouts":  timeouts_open,
        # new: full-pipeline funnel
        "funnel": {
            "title_clicked": scraper.get("title_passed_clicked", 0),
            "german_filtered": scraper.get("german_filtered", 0),
            "ai_matched": ai_matched,
            "ai_unmatched": unmatched,
            "applied": applied_total,
            # Same lookback as the Got interview chip: invite / round / offer,
            # including jobs later marked rejected or a side overlay.
            "interviewed": by_status.get(GOT_INTERVIEW_FILTER, 0),
            "offer": by_status.get("offer", 0),
        },
    })


@bp.route("/api/stats/daily")
@login_required
def api_stats_daily():
    """Return per-day activity counts for the calendar heatmap."""
    try:
        days = int(request.args.get("days", 120))
    except (TypeError, ValueError):
        days = 120
    days = max(7, min(days, 366))
    return jsonify({
        "days": get_daily_activity_stats(days=days, match_threshold=MATCH_THRESHOLD),
    })


@bp.route("/api/stats/locations")
@login_required
def api_stats_locations():
    """
    Location mix for the same application set as Application Journey.

    Cities are normalized (street/postal lines fold into the city; "X near City"
    keeps X). Country-only / pure-remote labels count as Remote; a concrete city
    still wins over a (Remote) work-mode tag. Only Germany + Remote locations
    are listed; `total` still counts every application.
    """
    return jsonify(get_applied_location_stats())


@bp.route("/api/stats/flow")
@login_required
def api_stats_flow():
    """
    Overview application-flow boards.

    Sankey: one current-state path per application record.
    Weekly bars: applications per ISO week by LinkedIn / Indeed / Other,
    including Reapplied steps (same counting as funnel Applied).
    """
    collection = get_collection("matched_jobs")
    jobs = list(collection.find(APPLICATION_QUERY, FLOW_PROJECTION))
    return jsonify({
        "sankey": build_sankey(jobs),
        "weekly": build_weekly_by_platform(jobs),
    })


@bp.route("/api/journey")
@login_required
def api_journey():
    """
    Application Journey:
    - Overview line: first apply date → today + total applied count
    - Company tracks: only jobs with interview invite / rounds / offer,
      each showing the full step timeline (Applied → Invite → Rounds …)
    """
    collection = get_collection("matched_jobs")
    jobs = list(collection.find(APPLICATION_QUERY, JOURNEY_PROJECTION))

    total_applied = 0
    first_applied = None
    tracks = []

    for job in jobs:
        jid = str(job.get("_id"))
        company = job.get("company") or "Unknown"
        title = job.get("title") or ""
        raw_timeline = job.get("application_timeline") if isinstance(job.get("application_timeline"), list) else []

        steps = []
        for item in raw_timeline:
            if not isinstance(item, dict):
                continue
            step = item.get("step")
            if step not in JOURNEY_TRACK_STEPS:
                continue
            at = _parse_timeline_at(item.get("at"))
            if not at:
                continue
            steps.append({
                "step": step,
                "label": TIMELINE_STEPS.get(step, step),
                "at": _format_timeline_at(at),
                "date": at.strftime("%Y-%m-%d"),
                "_dt": at,
            })

        # Apply date for tracks (may exist even if status later changed)
        applied_at = resolve_applied_at(job)

        # Same set as funnel / Locations: APPLICATION_QUERY (includes side
        # statuses that still have applied_at or a timeline)
        total_applied += 1 + reapplied_count(job)
        if applied_at and (first_applied is None or applied_at < first_applied):
            first_applied = applied_at

        if applied_at and not any(s["step"] == "applied" for s in steps):
            steps.insert(0, {
                "step": "applied",
                "label": TIMELINE_STEPS["applied"],
                "at": _format_timeline_at(applied_at),
                "date": applied_at.strftime("%Y-%m-%d"),
                "_dt": applied_at,
            })

        # Backfill old flat interview/offer status without detailed timeline
        if not any(s["step"] in JOURNEY_INTERVIEW_STEPS for s in steps):
            if job.get("status") == "interview":
                at = _parse_timeline_at(job.get("updated_at") or applied_at) or datetime.now()
                steps.append({
                    "step": "interview_invite",
                    "label": TIMELINE_STEPS["interview_invite"],
                    "at": _format_timeline_at(at),
                    "date": at.strftime("%Y-%m-%d"),
                    "_dt": at,
                })
            elif job.get("status") == "offer":
                at = _parse_timeline_at(job.get("updated_at") or applied_at) or datetime.now()
                steps.append({
                    "step": "offer",
                    "label": TIMELINE_STEPS["offer"],
                    "at": _format_timeline_at(at),
                    "date": at.strftime("%Y-%m-%d"),
                    "_dt": at,
                })

        has_interview = any(s["step"] in JOURNEY_INTERVIEW_STEPS for s in steps)
        if not has_interview:
            continue

        steps.sort(key=lambda s: s["_dt"])
        for s in steps:
            s.pop("_dt", None)

        last_dt = _parse_timeline_at(steps[-1]["at"]) if steps else None
        tracks.append({
            "job_id": jid,
            "company": company,
            "title": title,
            "status": job.get("status") or "pending",
            "steps": steps,
            "_sort": last_dt or datetime.min,
        })

    tracks.sort(key=lambda t: t["_sort"], reverse=True)
    for t in tracks:
        t.pop("_sort", None)

    today_dt = datetime.now()
    today = today_dt.strftime("%Y-%m-%d")
    start_date = first_applied.strftime("%Y-%m-%d") if first_applied else today

    # Axis end = later of today and the latest interview/event date (so future rounds sit on the scale)
    end_dt = today_dt
    for t in tracks:
        for s in t.get("steps") or []:
            at = _parse_timeline_at(s.get("at") or s.get("date"))
            if at and at > end_dt:
                end_dt = at
    end_date = end_dt.strftime("%Y-%m-%d")

    duration_days = 0
    if first_applied:
        duration_days = (end_dt.date() - first_applied.date()).days

    return jsonify({
        "start_date": start_date,
        "end_date": end_date,
        "today": today,
        "duration_days": duration_days,
        "total_applied": total_applied,
        "tracks": tracks,
    })
