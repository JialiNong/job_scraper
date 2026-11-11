"""Mongo helpers. Keep importing from here: `from core.db_mongo import …`.

Implementation lives in the `core.mongo` package (not a pile of mongo_*.py
siblings). Location labels stay in `applied_location.py` — they do not talk
to Mongo.
"""
from core.applied_location import (  # noqa: F401
    _city_in_text,
    is_germany_or_remote,
    normalize_applied_location,
)
from core.mongo.client import (  # noqa: F401
    COLLECTION_NAME,
    DATABASE_NAME,
    MONGO_URI,
    TIMEOUT_JOBS_COLLECTION,
    ensure_indexes,
    get_collection,
    get_db,
    get_mongo_client,
    init_db,
)
from core.mongo.jobs import (  # noqa: F401
    count_jobs,
    get_job_count,
    get_jobs,
    get_jobs_by_source,
    get_new_jobs,
    get_scraper_stats,
    increment_scraper_stat,
    is_job_id_exists,
    mark_job_as_matched,
    print_stats,
    save_job,
    update_job_status,
)
from core.mongo.lookup import (  # noqa: F401
    count_open_timeout_jobs,
    get_timeout_jobs,
    is_job_known,
    is_job_on_tracker,
    lookup_known_job,
    save_timeout_job,
)
from core.mongo.tracker import (  # noqa: F401
    APPLICATION_QUERY,
    APPLIED_STATUSES,
    _parse_filter_date,
    count_applied_applications,
    count_reapplied_events,
    count_unmatched_jobs,
    get_applied_job_keys,
    get_applied_location_stats,
    get_daily_activity_stats,
    get_unmatched_jobs,
    reapplied_count,
    resolve_applied_at,
)
from core.scraper_utils import indeed_job_id_variants  # noqa: F401  (manual_log re-export)


if __name__ == "__main__":
    print("Initializing MongoDB...")
    init_db()
    test_job = {
        "title": "Senior Frontend Engineer",
        "company": "Test Company",
        "location": "Berlin, Germany",
        "link": "https://example.com/job/12345",
        "job_id": "test_12345",
        "applicants": "50 applicants",
        "description": "We are looking for...",
        "source": "linkedin",
        "status": "new",
    }
    save_job(test_job)
    print_stats()
