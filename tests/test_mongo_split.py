"""db_mongo split: facade, known-job lookup, unmatched filters, heatmap.

Does not touch a live MongoDB — mocked collections only.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import core.db_mongo as db_mongo  # noqa: E402
from core.mongo.lookup import lookup_known_job  # noqa: E402
from core.mongo.tracker import (  # noqa: E402
    _parse_filter_date,
    get_daily_activity_stats,
    get_unmatched_jobs,
)


def _chain_find(docs):
    col = MagicMock()
    col.find.return_value.sort.return_value.skip.return_value.limit.return_value = docs
    col.count_documents.return_value = len(docs)
    return col


def _db(*, matched=None, jobs=None, timeouts=None):
    """Separate collection mocks — a single MagicMock shares every db['name']."""
    return {
        "matched_jobs": matched if matched is not None else MagicMock(),
        "jobs": jobs if jobs is not None else MagicMock(),
        "timeout_jobs": timeouts if timeouts is not None else MagicMock(),
    }


class FacadeTests(unittest.TestCase):
    def test_public_names_come_from_the_split_modules(self):
        import core.applied_location as loc
        import core.mongo.client as client
        import core.mongo.jobs as jobs
        import core.mongo.lookup as lookup
        import core.mongo.tracker as tracker

        self.assertIs(db_mongo.get_collection, client.get_collection)
        self.assertIs(db_mongo.save_job, jobs.save_job)
        self.assertIs(db_mongo.lookup_known_job, lookup.lookup_known_job)
        self.assertIs(db_mongo.get_unmatched_jobs, tracker.get_unmatched_jobs)
        self.assertIs(db_mongo.normalize_applied_location, loc.normalize_applied_location)
        self.assertEqual(db_mongo.APPLIED_STATUSES, tracker.APPLIED_STATUSES)


class LookupKnownJobTests(unittest.TestCase):
    def test_prefers_matched_jobs(self):
        matched = MagicMock()
        matched.find_one.return_value = {
            "_id": "m1",
            "title": "Backend Engineer",
            "company": "Acme",
            "status": "pending",
            "source": "linkedin",
            "job_id": "4100000001",
        }
        with patch("core.mongo.lookup.get_db", return_value=_db(matched=matched)):
            hit = lookup_known_job("4100000001", "linkedin")
        self.assertEqual(hit["stage"], "tracker")
        self.assertTrue(hit["on_tracker"])
        self.assertEqual(hit["title"], "Backend Engineer")

    def test_scored_jobs_row_is_unmatched(self):
        matched = MagicMock()
        matched.find_one.return_value = None
        jobs = MagicMock()
        jobs.find_one.return_value = {
            "_id": "u1",
            "title": "Java Engineer",
            "company": "Umbrella",
            "matched_at": datetime(2026, 11, 11, 12, 0),
            "match_score": 4.0,
            "source": "linkedin",
            "job_id": "4200000001",
        }
        with patch("core.mongo.lookup.get_db", return_value=_db(matched=matched, jobs=jobs)):
            hit = lookup_known_job("4200000001", "linkedin")
        self.assertEqual(hit["stage"], "unmatched")
        self.assertFalse(hit["on_tracker"])
        self.assertEqual(hit.get("match_gate"), "")

    def test_timeout_is_last(self):
        matched = MagicMock()
        matched.find_one.return_value = None
        jobs = MagicMock()
        jobs.find_one.return_value = None
        timeouts = MagicMock()
        timeouts.find_one.return_value = {
            "_id": "t1",
            "title": "Frontend Engineer",
            "company": "Hooli",
            "review_status": "open",
            "source": "linkedin",
            "job_id": "4300000001",
        }
        with patch(
            "core.mongo.lookup.get_db",
            return_value=_db(matched=matched, jobs=jobs, timeouts=timeouts),
        ):
            hit = lookup_known_job("4300000001", "linkedin")
        self.assertEqual(hit["stage"], "timeout")
        self.assertEqual(hit["collection"], "timeout_jobs")

    def test_posting_key_fallback(self):
        matched = MagicMock()
        matched.find_one.side_effect = [
            None,  # job_id miss
            {
                "_id": "m2",
                "title": "Same ad",
                "company": "Acme",
                "status": "pending",
                "source": "ats",
                "job_id": "other-id",
            },
        ]
        jobs = MagicMock()
        jobs.find_one.return_value = None
        timeouts = MagicMock()
        timeouts.find_one.return_value = None
        link = "https://jobs.ashbyhq.com/acme/abc"
        with patch(
            "core.mongo.lookup.get_db",
            return_value=_db(matched=matched, jobs=jobs, timeouts=timeouts),
        ):
            hit = lookup_known_job("missing", "linkedin", link=link)
        self.assertEqual(hit["matched_by"], "posting_key")
        self.assertEqual(hit["job_id"], "other-id")


class UnmatchedQueryTests(unittest.TestCase):
    def test_search_and_score_cap(self):
        col = _chain_find([{"title": "Java Engineer"}])
        with patch("core.mongo.tracker.get_db", return_value={"jobs": col}):
            jobs, total = get_unmatched_jobs(
                search="Java", threshold=7.0, score_max=9
            )
        self.assertEqual(total, 1)
        self.assertEqual(jobs[0]["title"], "Java Engineer")
        query = col.count_documents.call_args[0][0]
        self.assertEqual(query["match_score"]["$lt"], 7.0)
        self.assertLess(query["match_score"]["$lte"], 7.0)
        self.assertTrue(any("Java" in str(clause) for clause in query["$or"]))

    def test_agent_recommended_filters_and_dates_the_push_day(self):
        col = _chain_find([{"title": "Java Engineer"}])
        with patch("core.mongo.tracker.get_db", return_value={"jobs": col}):
            get_unmatched_jobs(
                agent_recommended=True,
                date_from="2026-11-13",
                date_to="2026-11-13",
            )
        query = col.count_documents.call_args[0][0]
        self.assertIn("agent_recommended_by", query)
        self.assertIn("agent_recommended_at", query)
        self.assertEqual(query["matched_at"], {"$exists": True})

    def test_parse_filter_date(self):
        self.assertIsNone(_parse_filter_date(""))
        start = _parse_filter_date("2026-11-11")
        end = _parse_filter_date("2026-11-11", end_of_day=True)
        self.assertEqual(start, datetime(2026, 11, 11, 0, 0))
        self.assertEqual(end.hour, 23)
        self.assertEqual(end.minute, 59)


class DailyActivityTests(unittest.TestCase):
    def test_heatmap_uses_daily_scraper_stats(self):
        today = datetime.now().strftime("%Y-%m-%d")

        class FakeColl:
            def __init__(self, find=(), aggregate=()):
                self._find = list(find)
                self._agg = list(aggregate)

            def find(self, *args, **kwargs):
                return self._find

            def aggregate(self, *args, **kwargs):
                return self._agg

        db = {
            "scraper_stats": FakeColl(find=[{
                "_id": f"daily_{today}",
                "date": today,
                "title_passed_clicked": 3,
                "german_filtered": 1,
            }]),
            "jobs": FakeColl(),
            "matched_jobs": FakeColl(),
        }
        with patch("core.mongo.tracker.get_db", return_value=db):
            rows = get_daily_activity_stats(days=3)
        by_date = {row["date"]: row for row in rows}
        self.assertEqual(len(rows), 3)
        self.assertEqual(by_date[today]["title_clicked"], 3)
        self.assertEqual(by_date[today]["german_filtered"], 1)
        self.assertEqual(by_date[today]["agent_unmatched"], 0)

    def test_heatmap_counts_agent_unmatched_by_push_day(self):
        today = datetime.now().strftime("%Y-%m-%d")

        class FakeColl:
            def __init__(self, find=(), aggregate=()):
                self._find = list(find)
                self._agg = list(aggregate)
                self.aggregate_calls = 0

            def find(self, *args, **kwargs):
                return self._find

            def aggregate(self, *args, **kwargs):
                self.aggregate_calls += 1
                return self._agg

        jobs = FakeColl(aggregate=[{"_id": today, "count": 2}])
        db = {
            "scraper_stats": FakeColl(find=[{
                "_id": f"daily_{today}",
                "date": today,
                "title_passed_clicked": 1,
                "german_filtered": 0,
            }]),
            "jobs": jobs,
            "matched_jobs": FakeColl(),
        }
        with patch("core.mongo.tracker.get_db", return_value=db):
            rows = get_daily_activity_stats(days=3)
        by_date = {row["date"]: row for row in rows}
        self.assertEqual(by_date[today]["agent_unmatched"], 2)
        self.assertGreaterEqual(jobs.aggregate_calls, 1)


if __name__ == "__main__":
    unittest.main()
