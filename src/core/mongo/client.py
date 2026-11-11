"""Mongo connection pool, collection accessors, and indexes.

Callers should keep importing from `core.db_mongo` unless they are another
`mongo_*.py` module in this package.
"""
from __future__ import annotations

import os
import threading

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

# Local test: mongodb://localhost:27017/
# MongoDB Atlas: mongodb+srv://username:password@cluster.mongodb.net/
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DATABASE_NAME = os.getenv("DATABASE_NAME", "job_scraper")
COLLECTION_NAME = "jobs"
TIMEOUT_JOBS_COLLECTION = "timeout_jobs"

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
        # API sources (src/sources/): company + title dedup across cities / reposts
        jobs.create_index("dedup_key", sparse=True)
        # Same ad under another URL / source (core.job_url.posting_key)
        jobs.create_index("posting_key", sparse=True)

        matched = db["matched_jobs"]
        matched.create_index([("matched_at", -1)])
        matched.create_index("status")
        matched.create_index([("status", 1), ("applied_at", -1)])
        matched.create_index("source")
        matched.create_index("origin")
        matched.create_index("run_id")
        matched.create_index("link")
        matched.create_index("posting_key", sparse=True)

        timeouts = db[TIMEOUT_JOBS_COLLECTION]
        timeouts.create_index("status")
        timeouts.create_index([("created_at", -1)])
        timeouts.create_index("posting_key", sparse=True)

        _indexes_ready = True


def init_db():
    """Initialize database and create indexes"""
    ensure_indexes()
    print(f"MongoDB initialized: {DATABASE_NAME}.{COLLECTION_NAME} (+ matched_jobs indexes)")
    print("Indexes ready for jobs, matched_jobs, timeout_jobs")
