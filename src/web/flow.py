"""Application-flow charts for Overview: Sankey sinks and weekly-by-platform.

Pure functions (no Flask, no Mongo). Classification uses the same timeline
hydration as Tracker progress chips, then the latest Reapplied step so a
restarted track is the current path.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from core.db_mongo import resolve_applied_at
from web.status import _hydrate_timeline, _parse_timeline_at

# ISO-week stacked bars: LinkedIn / Indeed stay named; everything else is Other.
PLATFORM_KEYS = ("linkedin", "indeed", "other")
PLATFORM_LABELS = {
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
    "other": "Other",
}

INTERVIEW_RANK = {
    "interview_invite": 0,
    "interview_1": 1,
    "interview_2": 2,
    "interview_3": 3,
    "interview_final": 4,
}

REJECTED_AT = {
    0: "rejected_invite",
    1: "rejected_1",
    2: "rejected_2",
    3: "rejected_later",
    4: "rejected_later",
}

# last interview step + whether its timestamp is still in the future
IN_PROGRESS_AT = {
    ("interview_invite", False): "awaiting",
    ("interview_invite", True): "awaiting",
    ("interview_1", True): "round_1_upcoming",
    ("interview_1", False): "round_1",
    ("interview_2", True): "round_2_upcoming",
    ("interview_2", False): "round_2",
    ("interview_3", True): "later_upcoming",
    ("interview_3", False): "later",
    ("interview_final", True): "later_upcoming",
    ("interview_final", False): "later",
}

# Terminals that never reached invite / a round / offer.
COL1_SINKS = frozenset({"waiting", "rejected"})

# id, English label, column, CSS tone (status colour token)
SANKY_NODE_META = (
    {"id": "applied", "label": "Applied", "column": 0, "tone": "applied"},
    {"id": "interview", "label": "Interview", "column": 1, "tone": "interview"},
    {"id": "waiting", "label": "Waiting", "column": 1, "tone": "waiting"},
    {"id": "rejected", "label": "Rejected", "column": 1, "tone": "rejected"},
    {"id": "awaiting", "label": "Awaiting interview", "column": 2, "tone": "interview"},
    {"id": "round_1_upcoming", "label": "Round 1 upcoming", "column": 2, "tone": "interview"},
    {"id": "round_1", "label": "After round 1", "column": 2, "tone": "interview"},
    {"id": "rejected_invite", "label": "Invite rejected", "column": 2, "tone": "rejected"},
    {"id": "rejected_1", "label": "Round 1 rejected", "column": 2, "tone": "rejected"},
    {"id": "round_2_upcoming", "label": "Round 2 upcoming", "column": 2, "tone": "interview"},
    {"id": "round_2", "label": "After round 2", "column": 2, "tone": "interview"},
    {"id": "rejected_2", "label": "Round 2 rejected", "column": 2, "tone": "rejected"},
    {"id": "later_upcoming", "label": "Later round upcoming", "column": 2, "tone": "interview"},
    {"id": "later", "label": "Later rounds", "column": 2, "tone": "interview"},
    {"id": "rejected_later", "label": "Later rejected", "column": 2, "tone": "rejected"},
    {"id": "offer", "label": "Offer", "column": 2, "tone": "offer"},
)

_NODE_BY_ID = {n["id"]: n for n in SANKY_NODE_META}


def platform_bucket(source) -> str:
    """linkedin / indeed stay named; ATS, manual, and APIs fold into other."""
    key = str(source or "").strip().lower()
    if key in ("linkedin", "indeed"):
        return key
    return "other"


def week_monday(value) -> date | None:
    """Monday of the ISO week that contains value, or None if unparseable."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        day = value.date()
    elif isinstance(value, date):
        day = value
    else:
        parsed = _parse_timeline_at(value)
        if not parsed:
            return None
        day = parsed.date()
    return day - timedelta(days=day.weekday())


def _events_after_reapply(job: dict) -> list[dict]:
    """Timeline events after the latest Reapplied (or the whole hydrated list)."""
    events = []
    for item in _hydrate_timeline(job):
        if isinstance(item, dict) and item.get("step"):
            events.append({"step": item["step"], "at": item.get("at")})
    last_re = None
    for i, event in enumerate(events):
        if event["step"] == "reapplied":
            last_re = i
    if last_re is None:
        return events
    rest = events[last_re + 1:]
    return rest if rest else [{"step": "applied", "at": None}]


def _naive(value) -> datetime | None:
    """Drop tzinfo so Atlas UTC datetimes compare with datetime.now()."""
    if value is None or value == "":
        return None
    dt = value if isinstance(value, datetime) else _parse_timeline_at(value)
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _is_upcoming(value, now: datetime) -> bool:
    dt = _naive(value)
    now_n = _naive(now) or datetime.now()
    return bool(dt and dt > now_n)


def classify_flow_sink(job: dict, now: datetime | None = None) -> str:
    """
    Current Sankey sink for one application record.

    Waiting / pre-interview rejected sit in column 1. Anything that reached
    invite / a round / offer continues through Interview into column 2.

    A round whose timestamp is still in the future is "upcoming"; a past
    timestamp means that round already happened and the job is waiting after
    it. Closed · No Reply without an interview counts as Waiting — it is the
    same "no response" bucket, just marked closed.
    """
    now = _naive(now) or datetime.now()
    events = _events_after_reapply(job)
    last = events[-1]["step"] if events else "applied"
    steps = [e["step"] for e in events]

    ranks = [INTERVIEW_RANK[s] for s in steps if s in INTERVIEW_RANK]
    has_offer = last == "offer" or "offer" in steps

    if has_offer and last != "rejected":
        return "offer"
    if last == "rejected":
        if not ranks:
            return "rejected"
        return REJECTED_AT[max(ranks)]
    if last in INTERVIEW_RANK:
        last_event = next((e for e in reversed(events) if e["step"] == last), None)
        upcoming = _is_upcoming(last_event.get("at") if last_event else None, now)
        return IN_PROGRESS_AT[(last, upcoming)]
    return "waiting"


def job_flow_links(job: dict, now: datetime | None = None) -> list[tuple[str, str]]:
    """Directed edges for this job's current path (one record, one path)."""
    sink = classify_flow_sink(job, now=now)
    if sink in COL1_SINKS:
        return [("applied", sink)]
    return [("applied", "interview"), ("interview", sink)]


def build_sankey(jobs: list, now: datetime | None = None) -> dict:
    """
    Aggregate APPLICATION_QUERY jobs into a 3-column Sankey.

    One path per job (current state). Reapplied events are not extra paths;
    they only restart which slice of the timeline counts. Zero-value nodes
    are omitted so an empty Offer slot does not draw.
    """
    now = now or datetime.now()
    link_counts: Counter = Counter()
    sink_counts: Counter = Counter()
    for job in jobs:
        sink = classify_flow_sink(job, now=now)
        sink_counts[sink] += 1
        for src, dst in job_flow_links(job, now=now):
            link_counts[(src, dst)] += 1
    interviewed = sum(
        count for sink, count in sink_counts.items() if sink not in COL1_SINKS
    )
    node_counts: Counter = Counter({"applied": len(jobs), "interview": interviewed})
    node_counts.update(sink_counts)

    nodes = []
    for meta in SANKY_NODE_META:
        value = int(node_counts.get(meta["id"], 0))
        if value <= 0 and meta["id"] != "applied":
            continue
        nodes.append({**meta, "value": value})
    if not jobs:
        nodes = [{**_NODE_BY_ID["applied"], "value": 0}]

    links = [
        {"source": src, "target": dst, "value": value}
        for (src, dst), value in link_counts.items()
        if value > 0
    ]
    return {
        "total": len(jobs),
        "nodes": nodes,
        "links": links,
    }


def _empty_platforms() -> dict:
    return {key: 0 for key in PLATFORM_KEYS}


def build_weekly_by_platform(jobs: list, today: date | None = None) -> dict:
    """
    Applications per ISO week (Monday start), stacked by platform.

    Same counting as the heatmap / funnel Applied total: one for the first
    apply date, plus one per Reapplied step on that step's day. Weeks with
    no applications still appear between the first apply and this week.
    """
    buckets: dict[date, dict] = defaultdict(_empty_platforms)
    for job in jobs:
        plat = platform_bucket(job.get("source"))
        applied_at = resolve_applied_at(job)
        monday = week_monday(applied_at)
        if monday:
            buckets[monday][plat] += 1
        raw = job.get("application_timeline")
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict) or item.get("step") != "reapplied":
                continue
            re_monday = week_monday(item.get("at"))
            if re_monday:
                buckets[re_monday][plat] += 1

    totals = _empty_platforms()
    if not buckets:
        return {"weeks": [], "totals": {**totals, "total": 0}}

    start = min(buckets)
    end = week_monday(today or date.today()) or start
    if end < start:
        end = start

    weeks = []
    cursor = start
    while cursor <= end:
        b = buckets[cursor]
        row = {
            "week_start": cursor.isoformat(),
            "linkedin": int(b["linkedin"]),
            "indeed": int(b["indeed"]),
            "other": int(b["other"]),
        }
        row["total"] = row["linkedin"] + row["indeed"] + row["other"]
        weeks.append(row)
        for key in PLATFORM_KEYS:
            totals[key] += row[key]
        cursor += timedelta(days=7)

    return {
        "weeks": weeks,
        "totals": {**totals, "total": sum(totals.values())},
    }
