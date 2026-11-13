"""Tracker reads: unmatched list, application counts, heatmap, location bubbles."""
from __future__ import annotations

from datetime import datetime

from core.applied_location import is_germany_or_remote, normalize_applied_location
from core.mongo.client import COLLECTION_NAME, get_collection, get_db


# Statuses that mean the user has submitted an application
APPLIED_STATUSES = ("applied", "rejected", "interview", "offer")

# Jobs that count as an application: current applied-like status, or a recorded
# apply date / timeline (status may later become closed / unsuitable / repost / duplicate).
APPLICATION_QUERY = {
    "$or": [
        {"applied_at": {"$ne": None}},
        {"status": {"$in": list(APPLIED_STATUSES)}},
        {"application_timeline.0": {"$exists": True}},
    ]
}


def _coerce_dt(value):
    """Parse a stored datetime / ISO / 'YYYY-MM-DD HH:MM' value, or None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def resolve_applied_at(job) -> datetime | None:
    """Same apply-date rules as the Application Journey total."""
    raw = job.get("application_timeline") if isinstance(job.get("application_timeline"), list) else []
    for item in raw:
        if not isinstance(item, dict) or item.get("step") != "applied":
            continue
        at = _coerce_dt(item.get("at"))
        if at:
            return at
    at = _coerce_dt(job.get("applied_at"))
    if at:
        return at
    if job.get("status") in APPLIED_STATUSES:
        return _coerce_dt(job.get("updated_at") or job.get("matched_at"))
    return None


def reapplied_count(job) -> int:
    """Reapplied steps on one job's timeline (each is one more application)."""
    raw = job.get("application_timeline") if isinstance(job.get("application_timeline"), list) else []
    return sum(1 for item in raw if isinstance(item, dict) and item.get("step") == "reapplied")


def count_reapplied_events() -> int:
    """Total Reapplied steps across all application records."""
    rows = list(get_collection("matched_jobs").aggregate([
        {"$match": {**APPLICATION_QUERY, "application_timeline.step": "reapplied"}},
        {"$unwind": "$application_timeline"},
        {"$match": {"application_timeline.step": "reapplied"}},
        {"$count": "n"},
    ]))
    return int(rows[0]["n"]) if rows else 0


def count_applied_applications() -> int:
    """
    Applications counted in funnel / Journey / Locations.

    One per application record — current APPLIED_STATUSES and jobs later
    marked with a side status (closed / unsuitable / repost / duplicate)
    that still have applied_at or an application timeline; pending (reset)
    is excluded — plus one per Reapplied step (applying again to the same
    job, e.g. a repost), matching the daily heatmap.
    """
    jobs = get_collection("matched_jobs").count_documents(APPLICATION_QUERY)
    return jobs + count_reapplied_events()


def get_applied_location_stats() -> dict:
    """
    Aggregate applications by normalized location.

    Same count as funnel Applied and Journey total_applied: APPLICATION_QUERY
    (applied-like status, or a recorded apply date / timeline) plus one per
    Reapplied step. Jobs later marked closed / unsuitable / repost /
    duplicate still count.

    Returns:
        {
          "total": int,
          "locations": [{"name": "Berlin", "count": 12}, ...],  # count desc
        }
    """
    from collections import Counter

    collection = get_collection("matched_jobs")
    counter: Counter = Counter()
    total = 0
    for doc in collection.find(
        APPLICATION_QUERY,
        {"location": 1, "application_timeline.step": 1},
    ):
        # A reapply is another application at the same place.
        n = 1 + reapplied_count(doc)
        total += n
        raw = doc.get("location") or ""
        name = normalize_applied_location(raw)
        # Chart shows Germany + Remote only; total still counts every application
        if is_germany_or_remote(raw, name):
            counter[name] += n

    locations = [
        {"name": name, "count": count}
        for name, count in counter.most_common()
    ]
    return {"total": total, "locations": locations}


def get_daily_activity_stats(days: int = 120, match_threshold: float = 7.0) -> list:
    """
    Build per-day activity stats for the activity calendar heatmap.

    Returns a list of dicts sorted by date ascending, covering the last
    `days` calendar days (including today).  Each item:

        {
          "date": "YYYY-MM-DD",
          "title_clicked": int,    # detail pages opened
          "german_filtered": int,  # non-English JDs skipped (UI: German Filtered)
          "ai_matched": int,       # jobs copied to matched_jobs
          "applied": int,          # applications dated by applied_at + Reapplied steps
          "agent_unmatched": int,  # agent push that day, still Unmatched
        }

    title_clicked / german_filtered come from daily scraper_stats docs.
    Days without a daily doc fall back to counting jobs.created_at for
    title_clicked (german_filtered stays 0 for those historical days).
    """
    from collections import defaultdict
    from datetime import timedelta

    days = max(1, min(int(days or 120), 366))
    now = datetime.now()
    start = (now - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    start_str = start.strftime("%Y-%m-%d")

    buckets = defaultdict(lambda: {
        "title_clicked": 0,
        "german_filtered": 0,
        "ai_matched": 0,
        "applied": 0,
        "agent_unmatched": 0,
        "_has_scraper_daily": False,
    })

    db = get_db()

    # 1) Daily scraper counters (clicks + german filters)
    for doc in db["scraper_stats"].find({"_id": {"$regex": r"^daily_"}}):
        date = doc.get("date") or str(doc["_id"]).replace("daily_", "", 1)
        if date < start_str:
            continue
        buckets[date]["title_clicked"] = int(doc.get("title_passed_clicked", 0) or 0)
        buckets[date]["german_filtered"] = int(doc.get("german_filtered", 0) or 0)
        buckets[date]["_has_scraper_daily"] = True

    # 2) Fallback: jobs scraped that day ≈ clicked (minus german, which we
    #    cannot recover historically)
    for row in db["jobs"].aggregate([
        {"$match": {"created_at": {"$gte": start, "$lte": end}}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
            "count": {"$sum": 1},
        }},
    ]):
        date = row["_id"]
        if date and not buckets[date]["_has_scraper_daily"]:
            buckets[date]["title_clicked"] = int(row["count"])

    # 3) AI matched (matched_jobs by matched_at)
    for row in db["matched_jobs"].aggregate([
        {"$match": {"matched_at": {"$gte": start, "$lte": end}}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$matched_at"}},
            "count": {"$sum": 1},
        }},
    ]):
        if row["_id"]:
            buckets[row["_id"]]["ai_matched"] = int(row["count"])

    # 4) Applied = any job with an application timestamp, dated by applied_at.
    #    Side statuses still count; pending (reset) has applied_at cleared.
    #    Do NOT fall back to updated_at: editing notes/highlights/status on an old
    #    applied job bumps updated_at and would inflate "applied today".
    for row in db["matched_jobs"].aggregate([
        {"$match": {
            "applied_at": {"$ne": None, "$gte": start, "$lte": end},
        }},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$applied_at"}},
            "count": {"$sum": 1},
        }},
    ]):
        if row["_id"]:
            buckets[row["_id"]]["applied"] = int(row["count"])

    # 4b) Reapplied steps are a new application on their own day (the job's
    #     applied_at keeps the first apply, so totals still count the job once).
    for row in db["matched_jobs"].aggregate([
        {"$match": {"application_timeline.step": "reapplied"}},
        {"$unwind": "$application_timeline"},
        {"$match": {
            "application_timeline.step": "reapplied",
            "application_timeline.at": {"$gte": start, "$lte": end},
        }},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$application_timeline.at"}},
            "count": {"$sum": 1},
        }},
    ]):
        if row["_id"]:
            buckets[row["_id"]]["applied"] += int(row["count"])

    # 5) Agent push that day for a job the matcher already kept on Unmatched.
    for row in db["jobs"].aggregate([
        {"$match": {
            "agent_recommended_at": {"$gte": start, "$lte": end},
            "matched_at": {"$exists": True},
            "match_score": {"$lt": float(match_threshold)},
        }},
        {"$group": {
            "_id": {
                "$dateToString": {
                    "format": "%Y-%m-%d",
                    "date": "$agent_recommended_at",
                }
            },
            "count": {"$sum": 1},
        }},
    ]):
        if row["_id"]:
            buckets[row["_id"]]["agent_unmatched"] = int(row["count"])

    # Fill every calendar day in range so the heatmap has no gaps
    out = []
    cursor = start
    while cursor <= end:
        date = cursor.strftime("%Y-%m-%d")
        b = buckets[date]
        out.append({
            "date": date,
            "title_clicked": b["title_clicked"],
            "german_filtered": b["german_filtered"],
            "ai_matched": b["ai_matched"],
            "applied": b["applied"],
            "agent_unmatched": b["agent_unmatched"],
        })
        cursor += timedelta(days=1)
    return out


def _parse_filter_date(value, end_of_day=False):
    """Parse YYYY-MM-DD or YYYY-MM-DDTHH:MM into a datetime, or None."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
            if end_of_day and fmt == "%Y-%m-%d":
                parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
            return parsed
        except ValueError:
            continue
    return None


def get_unmatched_jobs(
    page=1,
    page_size=20,
    source=None,
    search=None,
    threshold=7.0,
    date_from=None,
    date_to=None,
    score_min=None,
    score_max=None,
    user_status=None,
    match_gate=None,
    agent_recommended=False,
):
    """
    Return AI-analyzed jobs that scored below the match threshold.

    Newest matched_at first. Optional time range, score range, match_gate,
    and agent_recommended filters are applied on top of the unmatched
    threshold. When agent_recommended is on, the time range uses
    agent_recommended_at (the day an agent pushed the URL).
    """
    db = get_db()
    collection = db[COLLECTION_NAME]

    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 50))

    score_filter = {"$lt": float(threshold)}
    if score_min is not None:
        score_filter["$gte"] = float(score_min)
    if score_max is not None:
        score_filter["$lte"] = min(float(score_max), float(threshold) - 0.0001)

    filter_dict = {
        "matched_at": {"$exists": True},
        "match_score": score_filter,
    }

    if agent_recommended:
        filter_dict["agent_recommended_by"] = {"$exists": True, "$nin": ["", None]}

    start = _parse_filter_date(date_from)
    end = _parse_filter_date(date_to, end_of_day=True)
    if start or end:
        time_filter = {}
        if start:
            time_filter["$gte"] = start
        if end:
            time_filter["$lte"] = end
        date_key = "agent_recommended_at" if agent_recommended else "matched_at"
        filter_dict[date_key] = time_filter

    if source:
        filter_dict["source"] = source
    if match_gate:
        filter_dict["match_gate"] = match_gate
    if user_status == "watchlist":
        filter_dict["user_status"] = "watchlist"
    if search:
        filter_dict["$or"] = [
            {"title": {"$regex": search, "$options": "i"}},
            {"company": {"$regex": search, "$options": "i"}},
            {"disqualification_reason": {"$regex": search, "$options": "i"}},
            {"summary": {"$regex": search, "$options": "i"}},
        ]

    total = collection.count_documents(filter_dict)
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    page = min(page, pages)
    skip = (page - 1) * page_size
    projection = {"description": 0}
    jobs = list(
        collection.find(filter_dict, projection)
        .sort("matched_at", -1)
        .skip(skip)
        .limit(page_size)
    )
    return jobs, total


def count_unmatched_jobs(threshold=7.0):
    """Count AI-analyzed jobs that scored below the match threshold."""
    db = get_db()
    return db[COLLECTION_NAME].count_documents({
        "matched_at": {"$exists": True},
        "match_score": {"$lt": float(threshold)},
    })


def get_applied_job_keys():
    """
    Return set of (job_id, source) with an application record in matched_jobs.
    Used so manually-pursued low-score jobs keep their JD (including jobs later
    marked closed / unsuitable / repost / duplicate).
    """
    db = get_db()
    return {
        (doc.get("job_id"), doc.get("source"))
        for doc in db["matched_jobs"].find(
            APPLICATION_QUERY,
            {"job_id": 1, "source": 1},
        )
    }
