# db_mongo.py - MongoDB database operations
from pymongo import MongoClient, errors
from datetime import datetime
import os
import re
import threading
from dotenv import load_dotenv

from core.scraper_utils import indeed_job_id_variants

# Load environment variables from .env file
load_dotenv()

# MongoDB connection configuration
# Local test: mongodb://localhost:27017/
# MongoDB Atlas: mongodb+srv://username:password@cluster.mongodb.net/
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DATABASE_NAME = "job_scraper"
COLLECTION_NAME = "jobs"

# Reuse one client across the process. Creating MongoClient per call costs ~1–2s
# on Atlas (TLS + handshake) and made every Web UI refresh feel multi-second.
_mongo_client = None
_mongo_client_lock = threading.Lock()
_indexes_ready = False
_indexes_lock = threading.Lock()


def get_mongo_client() -> MongoClient:
    """Return a process-wide MongoClient (connection pool)."""
    global _mongo_client
    if _mongo_client is None:
        with _mongo_client_lock:
            if _mongo_client is None:
                _mongo_client = MongoClient(
                    MONGO_URI,
                    maxPoolSize=20,
                    serverSelectionTimeoutMS=8000,
                )
    return _mongo_client


def get_db():
    """Get database connection"""
    return get_mongo_client()[DATABASE_NAME]


def get_collection(collection_name=None):
    """
    Get a specific collection
    
    Args:
        collection_name (str): Name of the collection, defaults to main jobs collection
        
    Returns:
        Collection: MongoDB collection object
    """
    db = get_db()
    if collection_name is None:
        collection_name = COLLECTION_NAME
    return db[collection_name]


def ensure_indexes():
    """
    Create indexes used by scrapers and the Web UI.

    Safe to call repeatedly (create_index is idempotent). Web UI calls this
    once on startup so matched_jobs sorts / status counts stay indexed.
    """
    global _indexes_ready
    if _indexes_ready:
        return
    with _indexes_lock:
        if _indexes_ready:
            return
        db = get_db()
        jobs = db[COLLECTION_NAME]
        jobs.create_index("link", unique=True)
        jobs.create_index("job_id")
        jobs.create_index("source")
        jobs.create_index("created_at")
        jobs.create_index("matched_at")
        jobs.create_index("match_score")
        # Unmatched list / count: matched_at exists + score below threshold
        jobs.create_index([("match_score", 1), ("matched_at", -1)])

        matched = db["matched_jobs"]
        matched.create_index([("matched_at", -1)])
        matched.create_index("status")
        matched.create_index([("status", 1), ("applied_at", -1)])
        matched.create_index("source")
        matched.create_index("link")

        timeouts = db["timeout_jobs"]
        timeouts.create_index("status")
        timeouts.create_index([("created_at", -1)])

        _indexes_ready = True


def init_db():
    """Initialize database and create indexes"""
    ensure_indexes()
    print(f"MongoDB initialized: {DATABASE_NAME}.{COLLECTION_NAME} (+ matched_jobs indexes)")
    print("Indexes ready for jobs, matched_jobs, timeout_jobs")

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


TIMEOUT_JOBS_COLLECTION = "timeout_jobs"


def _timeout_id_query(job_id, source):
    """Match timeout / jobs docs, including Indeed jk variants."""
    query = {"job_id": job_id, "source": source}
    if source == "indeed":
        variants = indeed_job_id_variants(job_id)
        if variants:
            query = {"job_id": {"$in": variants}, "source": source}
    return query


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


# Statuses that mean the user has submitted an application
APPLIED_STATUSES = ("applied", "rejected", "interview", "offer")

# Parenthetical work-mode tags Often appended by LinkedIn / Indeed
_LOC_MODE_RE = re.compile(
    r"\s*[\(\[]\s*(remote|hybrid|on[\s-]?site|hybrides?\s+arbeiten)\s*[\)\]]\s*$",
    re.IGNORECASE,
)

# "Pullach near Munich" → keep the specific place before "near"
_NEAR_PLACE_RE = re.compile(
    r"^\s*(.+?)\s+near\s+\S+",
    re.IGNORECASE,
)

# Country / region only (no city) → count as Remote
_COUNTRY_ONLY = {
    "germany",
    "deutschland",
    "de",
    "eu",
    "europe",
    "european union",
    "european union (remote)",
    "dach",
    "emea",
}

# German states / non-city admin regions (not bubble labels by themselves)
_REGION_ONLY = {
    "bavaria",
    "bayern",
    "saxony",
    "sachsen",
    "hesse",
    "hessen",
    "lower saxony",
    "niedersachsen",
    "baden-württemberg",
    "baden-wuerttemberg",
    "baden württemberg",
    "north rhine-westphalia",
    "nordrhein-westfalen",
    "nrw",
    "rhineland-palatinate",
    "rheinland-pfalz",
    "schleswig-holstein",
    "mecklenburg-vorpommern",
    "brandenburg",
    "thuringia",
    "thüringen",
    "thueringen",
    "saarland",
    "uk",
    "ireland",
    "united kingdom",
}

# Known city aliases → canonical display name (lowercase keys)
_CITY_CANON = {
    "berlin": "Berlin",
    "berlin-kreuzberg": "Berlin",
    "kreuzberg": "Berlin",
    "munich": "Munich",
    "muenchen": "Munich",
    "münchen": "Munich",
    "hamburg": "Hamburg",
    "leipzig": "Leipzig",
    "cologne": "Cologne",
    "köln": "Cologne",
    "koeln": "Cologne",
    "frankfurt": "Frankfurt",
    "frankfurt am main": "Frankfurt",
    "stuttgart": "Stuttgart",
    "dresden": "Dresden",
    "nuremberg": "Nuremberg",
    "nürnberg": "Nuremberg",
    "nuernberg": "Nuremberg",
    "hannover": "Hannover",
    "hanover": "Hannover",
    "dortmund": "Dortmund",
    "augsburg": "Augsburg",
    "freiburg": "Freiburg",
    "ravensburg": "Ravensburg",
    "kronberg": "Kronberg",
    "böblingen": "Böblingen",
    "boeblingen": "Böblingen",
    "neu-ulm": "Neu-Ulm",
    "ulm": "Ulm",
    "mannheim": "Mannheim",
    "karlsruhe": "Karlsruhe",
    "heidelberg": "Heidelberg",
    "bonn": "Bonn",
    "düsseldorf": "Düsseldorf",
    "duesseldorf": "Düsseldorf",
    "dusseldorf": "Düsseldorf",
    "essen": "Essen",
    "bremen": "Bremen",
    "potsdam": "Potsdam",
    "wiesbaden": "Wiesbaden",
    "mainz": "Mainz",
    "kassel": "Kassel",
    "wuppertal": "Wuppertal",
}

# Longer aliases first so "berlin-kreuzberg" wins over "berlin"
_CITY_ALIASES_BY_LEN = tuple(
    sorted(_CITY_CANON.items(), key=lambda item: len(item[0]), reverse=True)
)


def _title_place(name: str) -> str:
    """Title-case a free-form place while keeping hyphenated parts."""
    parts = []
    for chunk in name.strip().split("-"):
        words = [w.capitalize() if w else w for w in chunk.split(" ")]
        parts.append(" ".join(words))
    return "-".join(parts)


def _is_country_or_region(token: str) -> bool:
    return token in _COUNTRY_ONLY or token in _REGION_ONLY


def _city_in_text(text_lower: str) -> str | None:
    """Return canonical city if a known alias appears as a whole word/token."""
    for alias, canon in _CITY_ALIASES_BY_LEN:
        if re.search(
            rf"(?<![a-z0-9äöüß]){re.escape(alias)}(?![a-z0-9äöüß])",
            text_lower,
        ):
            return canon
    return None


def normalize_applied_location(raw: str) -> str:
    """
    Normalize a job location for applied-job bubble charts.

    Rules:
      - Pure remote / home-office / country-only labels → Remote
      - A concrete city wins over work-mode tags like (Remote)/(Hybrid)
      - Street / postal lines that mention a known city (e.g. \"10407 Berlin\")
        count as that city, not Remote
      - \"X near City\" keeps the specific place X (does not fold into City)
      - Other named localities become their own bubble label
      - Work-mode suffixes are ignored for place detection
    """
    text = (raw or "").strip()
    if not text:
        return "Remote"

    lower = text.lower().strip()

    # Pure remote labels (any casing)
    if lower in {"remote", "homeoffice", "home office", "fully remote", "100% remote"}:
        return "Remote"
    if lower.startswith("remote ") or lower.startswith("remote-") or lower.startswith("remote/"):
        return "Remote"

    # Drop trailing work-mode tag for city / country detection
    cleaned = _LOC_MODE_RE.sub("", text).strip(" ,;-|")
    cleaned_lower = cleaned.lower()

    if not cleaned_lower or cleaned_lower in _COUNTRY_ONLY:
        return "Remote"

    # "Greater Munich Metropolitan Area", "Frankfurt Rhine-Main…"
    for key, canon in (
        ("munich", "Munich"),
        ("münchen", "Munich"),
        ("muenchen", "Munich"),
        ("frankfurt", "Frankfurt"),
        ("berlin", "Berlin"),
        ("hamburg", "Hamburg"),
        ("leipzig", "Leipzig"),
        ("cologne", "Cologne"),
        ("köln", "Cologne"),
        ("stuttgart", "Stuttgart"),
    ):
        if key in cleaned_lower and (
            "metropolitan" in cleaned_lower
            or "greater" in cleaned_lower
            or "rhine" in cleaned_lower
            or "area" in cleaned_lower
        ):
            return canon

    # "Pullach near Munich" → Pullach (specific place, not the nearby big city)
    near_match = _NEAR_PLACE_RE.match(cleaned)
    if near_match:
        specific = re.sub(r"\s+", " ", near_match.group(1).strip())
        specific_lower = specific.lower()
        if specific_lower in _CITY_CANON:
            return _CITY_CANON[specific_lower]
        if specific_lower and not _is_country_or_region(specific_lower):
            return _title_place(specific)

    # Scan comma / slash segments left-to-right for the first known city.
    # Also match city tokens anywhere in a segment so street/postal lines work:
    # "Kastanienallee 97, 10435 Berlin" / "10407 Berlin".
    segments = re.split(r"[,|/•·]|\bor\b", cleaned, flags=re.IGNORECASE)
    found = []
    for seg in segments:
        token = re.sub(r"\s+", " ", seg.strip().lower())
        if not token or _is_country_or_region(token):
            continue
        if token in _CITY_CANON:
            found.append(_CITY_CANON[token])
            continue
        # Whole-word city anywhere in the segment (street + PLZ + city)
        hit = _city_in_text(token)
        if hit:
            found.append(hit)
            continue
        for alias, canon in _CITY_ALIASES_BY_LEN:
            if token.startswith(alias + " ") or token.startswith(alias + "-"):
                found.append(canon)
                break

    if found:
        # Multiple distinct cities in one posting → keep first (primary)
        return found[0]

    # No concrete city, but the label still says remote (e.g. "European Remote")
    if re.search(r"(?<![a-z0-9])remote(?![a-z0-9])", cleaned_lower):
        return "Remote"

    # Named locality without a known-city alias → keep that place as its own bubble
    for seg in segments:
        token = re.sub(r"\s+", " ", seg.strip())
        if not token:
            continue
        token_lower = token.lower()
        if _is_country_or_region(token_lower):
            continue
        # Skip pure street lines with house numbers but no remaining place word
        if re.fullmatch(r"[\w.\-äöüÄÖÜß]+\s+\d+[a-zA-Z]?", token):
            continue
        return _title_place(token)

    return "Remote"


def get_applied_location_stats() -> dict:
    """
    Aggregate applied-like jobs (applied/rejected/interview/offer) by normalized location.

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
        {"status": {"$in": list(APPLIED_STATUSES)}},
        {"location": 1},
    ):
        total += 1
        counter[normalize_applied_location(doc.get("location") or "")] += 1

    locations = [
        {"name": name, "count": count}
        for name, count in counter.most_common()
    ]
    return {"total": total, "locations": locations}


def get_daily_activity_stats(days: int = 120) -> list:
    """
    Build per-day activity stats for the activity calendar heatmap.

    Returns a list of dicts sorted by date ascending, covering the last
    `days` calendar days (including today).  Each item:

        {
          "date": "YYYY-MM-DD",
          "title_clicked": int,    # detail pages opened
          "german_filtered": int,  # non-English JDs skipped (UI: German Filtered)
          "ai_matched": int,       # jobs copied to matched_jobs
          "applied": int,          # applied + rejected + interview + offer (by applied_at)
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

    # 4) Applied = applied + rejected + interview + offer, dated by applied_at only.
    #    Do NOT fall back to updated_at: editing notes/highlights/status on an old
    #    applied job bumps updated_at and would inflate "applied today".
    for row in db["matched_jobs"].aggregate([
        {"$match": {
            "status": {"$in": list(APPLIED_STATUSES)},
            "applied_at": {"$ne": None, "$gte": start, "$lte": end},
        }},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$applied_at"}},
            "count": {"$sum": 1},
        }},
    ]):
        if row["_id"]:
            buckets[row["_id"]]["applied"] = int(row["count"])

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
        })
        cursor += timedelta(days=1)
    return out


def mark_job_as_matched(
    job_id,
    source,
    match_score=None,
    analysis=None,
    clear_description=False,
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
        clear_description (bool): Drop JD text (stack-gate stubs keep title + link)

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
            "requirement_extract": analysis.get("requirement_extract") or {},
        })

    if clear_description:
        update_data["description"] = ""
        update_data["description_cleared_at"] = datetime.now()
        # Extract quotes duplicate the JD; unmatched review only needs title + link.
        update_data["requirement_extract"] = {}

    result = collection.update_one(
        {"job_id": job_id, "source": source},
        {"$set": update_data}
    )

    return result.modified_count > 0


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
):
    """
    Return AI-analyzed jobs that scored below the match threshold.

    Newest matched_at first. Optional time range, score range, and match_gate
    filters are applied on top of the unmatched threshold.
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

    start = _parse_filter_date(date_from)
    end = _parse_filter_date(date_to, end_of_day=True)
    if start or end:
        time_filter = {}
        if start:
            time_filter["$gte"] = start
        if end:
            time_filter["$lte"] = end
        filter_dict["matched_at"] = time_filter

    if source:
        filter_dict["source"] = source
    if match_gate:
        filter_dict["match_gate"] = match_gate
    if user_status == "watchlist":
        filter_dict["user_status"] = "watchlist""
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
    Return set of (job_id, source) already applied / rejected / interviewed
    in matched_jobs. Used so manually-pursued low-score jobs keep their JD.
    """
    db = get_db()
    return {
        (doc.get("job_id"), doc.get("source"))
        for doc in db["matched_jobs"].find(
            {"status": {"$in": list(APPLIED_STATUSES)}},
            {"job_id": 1, "source": 1},
        )
    }


def clear_unmatched_descriptions(days=14, threshold=7.0, dry_run=False):
    """
    Clear the description field on unmatched jobs older than `days`.

    Keeps link, title, company, and all AI analysis fields. Skips:
    - watchlist (Can Apply) jobs — so a later re-copy still has the JD
    - jobs already applied / rejected / interviewed in matched_jobs
      (low score but you manually decided to pursue them)

    Safe to run daily — only documents with a non-empty description and
    matched_at older than the window are updated.

    Args:
        days (int): Age threshold based on matched_at (default 14)
        threshold (float): Match score below which a job counts as unmatched
        dry_run (bool): If True, only count matching docs without updating

    Returns:
        int: Number of jobs that would be / were cleared
    """
    from datetime import timedelta

    db = get_db()
    collection = db[COLLECTION_NAME]
    cutoff = datetime.now() - timedelta(days=days)
    protected = get_applied_job_keys()

    filter_dict = {
        "matched_at": {"$exists": True, "$lt": cutoff},
        "match_score": {"$lt": float(threshold)},
        "user_status": {"$ne": "watchlist"},
        "description": {"$exists": True, "$nin": [None, ""]},
    }

    candidates = list(
        collection.find(filter_dict, {"_id": 1, "job_id": 1, "source": 1})
    )
    ids_to_clear = [
        doc["_id"]
        for doc in candidates
        if (doc.get("job_id"), doc.get("source")) not in protected
    ]
    skipped_applied = len(candidates) - len(ids_to_clear)

    if dry_run:
        return len(ids_to_clear)

    if not ids_to_clear:
        print(
            f"🧹 Cleared description on 0 unmatched jobs older than {days} days"
            + (f" (skipped {skipped_applied} applied/rejected/interview)" if skipped_applied else "")
        )
        return 0

    result = collection.update_many(
        {"_id": {"$in": ids_to_clear}},
        {
            "$set": {
                "description": "",
                "description_cleared_at": datetime.now(),
                "updated_at": datetime.now(),
            }
        },
    )
    skip_note = (
        f" (skipped {skipped_applied} applied/rejected/interview)"
        if skipped_applied
        else ""
    )
    print(
        f"🧹 Cleared description on {result.modified_count} unmatched jobs "
        f"older than {days} days{skip_note}"
    )
    return result.modified_count


def delete_old_jobs(days=30):
    """
    Delete jobs older than specified days (optional cleanup)
    
    Args:
        days (int): Number of days threshold
        
    Returns:
        int: Number of deleted jobs
    """
    from datetime import timedelta
    
    db = get_db()
    collection = db[COLLECTION_NAME]
    
    threshold = datetime.now() - timedelta(days=days)
    result = collection.delete_many({"created_at": {"$lt": threshold}})
    
    print(f"🗑️  Deleted {result.deleted_count} jobs older than {days} days")
    return result.deleted_count


# Testing and statistics
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


if __name__ == "__main__":
    # Test connection and initialization
    print("Initializing MongoDB...")
    init_db()
    
    # Test saving data
    test_job = {
        "title": "Senior Frontend Engineer",
        "company": "Test Company",
        "location": "Berlin, Germany",
        "link": "https://example.com/job/12345",
        "job_id": "test_12345",
        "applicants": "50 applicants",
        "description": "We are looking for...",
        "source": "linkedin",
        "status": "new"
    }
    
    save_job(test_job)
    
    # Print statistics
    print_stats()
