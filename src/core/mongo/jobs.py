"""Scrape-store (`jobs`) writes: save, query, matcher write-back, scraper counters."""
from __future__ import annotations

from datetime import datetime

from pymongo import errors

from core.job_url import posting_key_fields
from core.mongo.client import COLLECTION_NAME, get_db
from core.scraper_utils import indeed_job_id_variants


def save_job(job_data):
    """
    Save job data to MongoDB
    
    Args:
        job_data (dict): Job information dictionary
        
    Returns:
        bool: True if saved successfully, False if already exists or failed
    """
    db = get_db()
    collection = db[COLLECTION_NAME]
    
    # Add creation timestamp
    job_data["created_at"] = datetime.now()
    
    # Ensure required fields have default values
    job_data.setdefault("source", "linkedin")
    job_data.setdefault("status", "new")
    if not job_data.get("posting_key"):
        job_data.update(posting_key_fields(job_data.get("link")))
    
    try:
        result = collection.insert_one(job_data)
        print(f"✅ Saved job: {job_data.get('title', 'Untitled')} (ID: {result.inserted_id})")
        return True
    except errors.DuplicateKeyError:
        print(f"⚠️  Job already exists: {job_data.get('title', 'Untitled')}")
        return False
    except Exception as e:
        print(f"❌ Error saving job: {e}")
        return False


def is_job_id_exists(job_id, source="linkedin"):
    """
    Check if job_id already exists
    
    Args:
        job_id (str): Job ID
        source (str): Job source website
        
    Returns:
        bool: True if exists, False otherwise
    """
    db = get_db()
    collection = db[COLLECTION_NAME]

    # Indeed historically stored DOM ids with job_/sj_ prefixes; accept all forms.
    if source == "indeed":
        variants = indeed_job_id_variants(job_id)
        if variants:
            count = collection.count_documents(
                {"job_id": {"$in": variants}, "source": source}
            )
            return count > 0

    count = collection.count_documents({"job_id": job_id, "source": source})
    return count > 0


def get_jobs(filter_dict=None, limit=100):
    """
    Query job data
    
    Args:
        filter_dict (dict): Query filter, e.g. {"source": "linkedin"}
        limit (int): Maximum number of results to return
        
    Returns:
        list: List of job documents
    """
    db = get_db()
    collection = db[COLLECTION_NAME]
    
    if filter_dict is None:
        filter_dict = {}
    
    jobs = list(collection.find(filter_dict).sort("created_at", -1).limit(limit))
    return jobs


def get_new_jobs(limit=None, source=None):
    """
    Get jobs with status "new" that haven't been analyzed yet
    
    Args:
        limit (int): Maximum number of jobs to return (None for all)
        source (str): Filter by source (indeed, linkedin, or None for all)
        
    Returns:
        list: List of new job documents
    """
    db = get_db()
    collection = db[COLLECTION_NAME]
    
    # Only query jobs with status="new" and without matched_at field (not yet AI matched)
    filter_dict = {
        "status": "new",
        "matched_at": {"$exists": False}  # New: exclude already matched jobs
    }
    if source:
        filter_dict["source"] = source
    
    query = collection.find(filter_dict).sort("created_at", -1)
    
    if limit:
        query = query.limit(limit)
    
    return list(query)


def get_jobs_by_source(source, limit=100):
    """
    Get jobs by source
    
    Args:
        source (str): Job source (indeed, linkedin)
        limit (int): Maximum number of results
        
    Returns:
        list: List of job documents
    """
    return get_jobs(filter_dict={"source": source}, limit=limit)


def count_jobs(source=None):
    """
    Count jobs, optionally filtered by source
    
    Args:
        source (str): Job source (indeed, linkedin, or None for all)
        
    Returns:
        int: Number of jobs
    """
    filter_dict = {"source": source} if source else {}
    return get_job_count(filter_dict)


def get_job_count(filter_dict=None):
    """
    Count jobs matching the filter
    
    Args:
        filter_dict (dict): Query filter
        
    Returns:
        int: Number of jobs
    """
    db = get_db()
    collection = db[COLLECTION_NAME]
    
    if filter_dict is None:
        filter_dict = {}
    
    return collection.count_documents(filter_dict)


def update_job_status(job_id, source, new_status):
    """
    Update job status
    
    Args:
        job_id (str): Job ID
        source (str): Job source website
        new_status (str): New status, e.g. "applied", "interviewed", "rejected"
        
    Returns:
        bool: True if updated successfully
    """
    db = get_db()
    collection = db[COLLECTION_NAME]
    
    result = collection.update_one(
        {"job_id": job_id, "source": source},
        {"$set": {"status": new_status, "updated_at": datetime.now()}}
    )
    
    return result.modified_count > 0


def increment_scraper_stat(key: str, amount: int = 1) -> None:
    """
    Atomically increment a global scraper counter by `amount`.

    Also increments the same key on today's daily document
    (_id="daily_YYYY-MM-DD") so the activity calendar can show per-day counts.

    Recognised keys
    ---------------
    title_passed_clicked  – jobs that passed the title filter and were new
                            (i.e. we actually clicked into the detail page)
    ai_title_filtered     – titles without DEFAULT_KEYWORDS that AI judged
                            unrelated to target roles (skipped before click)
    german_filtered       – detail pages not in English (mostly German; FR etc.
                            rare) and skipped; UI still labels this bucket
                            "German Filtered"
    detail_timeout        – title passed, but the JD panel did not load in time
    """
    db = get_db()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    db["scraper_stats"].update_one(
        {"_id": "global"},
        {"$inc": {key: amount}, "$set": {"updated_at": now}},
        upsert=True,
    )
    db["scraper_stats"].update_one(
        {"_id": f"daily_{today}"},
        {
            "$inc": {key: amount},
            "$set": {"date": today, "updated_at": now},
        },
        upsert=True,
    )


def get_scraper_stats() -> dict:
    """
    Return all scraper counters as a plain dict (keys without leading '_').

    Returns an empty dict if no scraping run has taken place yet.
    """
    db = get_db()
    doc = db["scraper_stats"].find_one({"_id": "global"}) or {}
    doc.pop("_id", None)
    doc.pop("updated_at", None)
    return doc


def mark_job_as_matched(
    job_id,
    source,
    match_score=None,
    analysis=None,
):
    """
    Mark a job as AI-analyzed and persist the full analysis on the jobs document.

    Matched jobs are also copied to matched_jobs separately. Unmatched jobs stay
    here so the unmatched page can show score, reason, and breakdowns.

    Args:
        job_id (str): Job ID
        source (str): Job source website
        match_score (float): Match score (0-10)
        analysis (dict): Full AI analysis payload to store on the job

    Returns:
        bool: True if updated successfully
    """
    db = get_db()
    collection = db[COLLECTION_NAME]

    update_data = {
        "matched_at": datetime.now(),
        "updated_at": datetime.now(),
    }

    if match_score is not None:
        update_data["match_score"] = match_score

    if analysis:
        update_data.update({
            "recommendation": analysis.get("recommendation", ""),
            "special_match": bool(analysis.get("special_match")),
            "special_match_reasons": analysis.get("special_match_reasons") or [],
            "disqualification_reason": analysis.get("disqualification_reason", ""),
            "match_reasons": analysis.get("match_reasons", []),
            "missing_requirements": analysis.get("missing_requirements", []),
            "red_flags": analysis.get("red_flags", []),
            "nice_to_have_matches": analysis.get("nice_to_have_matches", []),
            "summary": analysis.get("summary", ""),
            "what_youll_do": analysis.get("what_youll_do") or {"matched": [], "unmatched": []},
            "what_theyre_looking_for": analysis.get("what_theyre_looking_for") or {"matched": [], "unmatched": []},
            "match_gate": analysis.get("match_gate") or "",
            "needs_refetch": bool(analysis.get("needs_refetch")),
            "job_labels": analysis.get("job_labels") or {},
            "company_profile": analysis.get("company_profile") or {},
        })

    result = collection.update_one(
        {"job_id": job_id, "source": source},
        {"$set": update_data}
    )

    return result.modified_count > 0


def print_stats():
    """Print database statistics"""
    db = get_db()
    collection = db[COLLECTION_NAME]
    
    total = collection.count_documents({})
    linkedin_count = collection.count_documents({"source": "linkedin"})
    indeed_count = collection.count_documents({"source": "indeed"})
    
    print("\n📊 Database Statistics:")
    print(f"Total jobs: {total}")
    print(f"LinkedIn jobs: {linkedin_count}")
    print(f"Indeed jobs: {indeed_count}")
    
    # Group by status
    pipeline = [
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}
    ]
    status_stats = list(collection.aggregate(pipeline))
    
    print("\nBy status:")
    for stat in status_stats:
        print(f"  {stat['_id']}: {stat['count']}")
