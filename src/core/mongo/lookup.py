"""Known-job lookup across jobs / matched_jobs / timeout_jobs, plus timeout review."""
from __future__ import annotations

from datetime import datetime

from core.job_url import posting_key, posting_key_fields
from core.mongo.client import COLLECTION_NAME, TIMEOUT_JOBS_COLLECTION, get_db
from core.mongo.jobs import increment_scraper_stat
from core.scraper_utils import indeed_job_id_variants


def _timeout_id_query(job_id, source):
    """Match timeout / jobs docs, including Indeed jk variants."""
    query = {"job_id": job_id, "source": source}
    if source == "indeed":
        variants = indeed_job_id_variants(job_id)
        if variants:
            query = {"job_id": {"$in": variants}, "source": source}
    return query


def is_job_known(job_id, source="linkedin", link=None):
    """
    True if job_id is already in `jobs` or `matched_jobs` (Indeed: all jk variants).
    With `link`, also True when the same ad was saved under another id / source.
    Used by scrapers to skip re-clicking a listing already stored.
    """
    return lookup_known_job(job_id, source, link=link) is not None


def is_job_on_tracker(job_id, source="linkedin"):
    """
    True if job_id is already in `matched_jobs` (Indeed: all jk variants).

    Manual Log Job only skips when the URL is already on Tracker. A row that
    exists only in `jobs` (awaiting AI or scored unmatched) is still promotable.
    """
    hit = lookup_known_job(job_id, source)
    return bool(hit and hit.get("on_tracker"))


def lookup_known_job(job_id, source="linkedin", link=None):
    """
    Find a job by job_id + source (Indeed: all jk variants).

    With `link`, a miss falls back to the link's posting key, so the same ad
    saved under another URL or source (manual Personio link vs. ATS source,
    `?language=en` copies …) is found too. Such hits carry
    matched_by="posting_key" and the existing row's own job_id / source.

    Prefers `matched_jobs` (Tracker), then `jobs` (scrape store),
    then `timeout_jobs` still pending review.

    Returns:
        dict with keys collection, stage, on_tracker, _id, title, company,
        status, source, job_id, match_score — or None if not found.
        Tracker hits also include ai_pending, details_fetched_at, summary,
        and description (for Log Job refresh / details_missing).

        stage:
          - tracker: already on matched_jobs
          - unmatched: in jobs with matched_at (AI scored below threshold)
          - awaiting_match: in jobs, not yet AI-matched (invisible on Unmatched)
          - timeout: pending timeout review
    """
    job_id = str(job_id or "").strip()
    source = (source or "").strip().lower()
    if job_id and source:
        hit = _lookup_by_query(_timeout_id_query(job_id, source), job_id, source)
        if hit:
            hit["matched_by"] = "job_id"
            return hit
    key = posting_key(link) if link else None
    if key:
        hit = _lookup_by_query({"posting_key": key}, job_id, source)
        if hit:
            hit["matched_by"] = "posting_key"
            return hit
    return None


def _lookup_by_query(id_query, job_id, source):
    """lookup_known_job body: matched_jobs → jobs → pending timeout_jobs."""
    db = get_db()
    projection = {
        "_id": 1,
        "title": 1,
        "company": 1,
        "status": 1,
        "source": 1,
        "job_id": 1,
        "user_status": 1,
        "review_status": 1,
        "matched_at": 1,
        "match_score": 1,
        "ai_pending": 1,
        "details_fetched_at": 1,
        "summary": 1,
        "description": 1,
    }

    matched = db["matched_jobs"].find_one(id_query, projection)
    if matched:
        return {
            "collection": "matched_jobs",
            "stage": "tracker",
            "on_tracker": True,
            "_id": str(matched["_id"]),
            "title": matched.get("title") or "",
            "company": matched.get("company") or "",
            "status": matched.get("status") or "pending",
            "source": matched.get("source") or source,
            "job_id": matched.get("job_id") or job_id,
            "match_score": matched.get("match_score"),
            "ai_pending": bool(matched.get("ai_pending")),
            "details_fetched_at": matched.get("details_fetched_at"),
            "summary": matched.get("summary") or "",
            "description": matched.get("description") or "",
        }

    scraped = db[COLLECTION_NAME].find_one(id_query, projection)
    if scraped:
        has_matched_at = scraped.get("matched_at") is not None
        stage = "unmatched" if has_matched_at else "awaiting_match"
        return {
            "collection": "jobs",
            "stage": stage,
            "on_tracker": False,
            "_id": str(scraped["_id"]),
            "title": scraped.get("title") or "",
            "company": scraped.get("company") or "",
            "status": scraped.get("user_status") or scraped.get("status") or "",
            "source": scraped.get("source") or source,
            "job_id": scraped.get("job_id") or job_id,
            "match_score": scraped.get("match_score"),
        }

    timeout = db[TIMEOUT_JOBS_COLLECTION].find_one(
        {**id_query, "review_status": {"$ne": "added"}},
        projection,
    )
    if timeout:
        return {
            "collection": "timeout_jobs",
            "stage": "timeout",
            "on_tracker": False,
            "_id": str(timeout["_id"]),
            "title": timeout.get("title") or "",
            "company": timeout.get("company") or "",
            "status": timeout.get("review_status") or "pending",
            "source": timeout.get("source") or source,
            "job_id": timeout.get("job_id") or job_id,
            "match_score": None,
        }

    return None


def save_timeout_job(
    *,
    title,
    job_id,
    link,
    source,
    company="",
    location="",
    keyword="",
    reason="detail_timeout",
) -> bool:
    """
    Store a title-qualified card whose detail panel timed out.

    Only records jobs with a title + job_id (so you can open the link later).
    Skips if the job is already in `jobs` or `matched_jobs`.
    """
    title = (title or "").strip()
    job_id = str(job_id or "").strip()
    source = (source or "").strip().lower()
    if not title or not job_id or not source:
        return False

    db = get_db()
    id_query = _timeout_id_query(job_id, source)
    if db[COLLECTION_NAME].find_one(id_query, {"_id": 1}):
        return False
    if db["matched_jobs"].find_one(id_query, {"_id": 1}):
        return False

    now = datetime.now()
    col = db[TIMEOUT_JOBS_COLLECTION]
    existing = col.find_one(id_query)
    if existing and existing.get("review_status") == "added":
        return False

    link = (link or "").strip()
    if source == "indeed" and job_id and (not link or not link.startswith("http")):
        link = f"https://de.indeed.com/viewjob?jk={job_id}"
    if source == "linkedin" and job_id:
        link = f"https://www.linkedin.com/jobs/view/{job_id}/"

    doc = {
        "title": title,
        "job_id": job_id,
        "link": link,
        "source": source,
        "company": (company or "").strip(),
        "location": (location or "").strip(),
        "keyword": (keyword or "").strip(),
        "reason": reason,
        "review_status": "open",
        "updated_at": now,
        **posting_key_fields(link),
    }
    if existing:
        col.update_one({"_id": existing["_id"]}, {"$set": doc, "$inc": {"timeout_count": 1}})
    else:
        doc["created_at"] = now
        doc["timeout_count"] = 1
        col.insert_one(doc)
    increment_scraper_stat("detail_timeout")
    print(f"Job timeout saved for review: {title} ({source}/{job_id})")
    return True


def get_timeout_jobs(page=1, page_size=50, source=None, review_status="open"):
    """Newest timeout cards first. Returns (jobs, total)."""
    db = get_db()
    col = db[TIMEOUT_JOBS_COLLECTION]
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    query = {}
    if source and source != "all":
        query["source"] = source
    if review_status and review_status != "all":
        query["review_status"] = review_status
    total = col.count_documents(query)
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    page = min(page, pages)
    skip = (page - 1) * page_size
    jobs = list(col.find(query).sort("created_at", -1).skip(skip).limit(page_size))
    return jobs, total


def count_open_timeout_jobs() -> int:
    """Count timeout cards still waiting for review."""
    db = get_db()
    return db[TIMEOUT_JOBS_COLLECTION].count_documents({"review_status": "open"})
