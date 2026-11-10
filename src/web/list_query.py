"""Mongo queries for the Tracker job list filters (shared by list + batch select)."""
import re

from core.country import country_filter_query as jobs_country_filter_query
from core.db_mongo import _parse_filter_date
from core.job_origin import normalize_origin, origin_filter_query
from web.status import jobs_status_filter_query


# List payload omits full JD text (often multi-KB per job). Lazy-load via
# GET /api/jobs/<id>/description when the user expands "Original description".
JOBS_LIST_PROJECTION = {"description": 0}


def combine_job_list_query(clauses: list) -> dict:
    """AND independent list filters so a status $or does not clash with search $or."""
    parts = [c for c in clauses if c]
    if not parts:
        return {}
    if len(parts) == 1:
        return parts[0]
    return {"$and": parts}


def company_match_clause(company: str) -> dict:
    """Case-insensitive exact company match (ignores surrounding whitespace)."""
    name = re.sub(r"\s+", " ", str(company or "")).strip()
    if not name:
        return {}
    pattern = r"^\s*" + r"\s+".join(re.escape(part) for part in name.split(" ")) + r"\s*$"
    return {"company": {"$regex": pattern, "$options": "i"}}


def _company_key(company) -> str:
    return re.sub(r"\s+", " ", str(company or "")).strip().casefold()


def _jobs_list_query_from_args(args) -> dict:
    """Mongo query for Tracker list filters (status, source, origin, country, search, company, dates, applied day, highlights)."""
    status_filter = args.get("status")
    source_filter = args.get("source")
    country_filter = args.get("country")
    search_query = (args.get("q") or "").strip()
    company_filter = (args.get("company") or "").strip()
    date_from = (args.get("date_from") or "").strip() or None
    date_to = (args.get("date_to") or "").strip() or None
    applied_on = (args.get("applied_on") or "").strip()
    highlights_filter = (args.get("highlights") or "").strip().lower()
    origin_filter = normalize_origin(args.get("origin"))
    clauses = [jobs_status_filter_query(status_filter)]
    if source_filter and source_filter != "all":
        clauses.append({"source": source_filter})
    clauses.append(jobs_country_filter_query(country_filter))
    if origin_filter:
        clauses.append(origin_filter_query(origin_filter))
    if highlights_filter in ("1", "true", "yes"):
        clauses.append({"highlights.0": {"$exists": True}})
    if search_query:
        clauses.append({
            "$or": [
                {"title": {"$regex": search_query, "$options": "i"}},
                {"company": {"$regex": search_query, "$options": "i"}},
            ]
        })
    if company_filter:
        clauses.append(company_match_clause(company_filter))
    if date_from or date_to:
        time_filter: dict = {}
        start = _parse_filter_date(date_from)
        end = _parse_filter_date(date_to, end_of_day=True)
        if start:
            time_filter["$gte"] = start
        if end:
            time_filter["$lte"] = end
        if time_filter:
            clauses.append({"matched_at": time_filter})
    if applied_on:
        # Same basis as the heatmap's Applied count: applied_at that day, or a
        # Reapplied step that day
        start = _parse_filter_date(applied_on)
        end = _parse_filter_date(applied_on, end_of_day=True)
        if start and end:
            day = {"$gte": start, "$lte": end}
            clauses.append({"$or": [
                {"applied_at": day},
                {"application_timeline": {"$elemMatch": {"step": "reapplied", "at": day}}},
            ]})
    return combine_job_list_query(clauses)
