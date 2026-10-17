"""Tracker stage filters follow application progress, not only status.

Does not touch a live MongoDB.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import core.db_mongo as db_mongo  # noqa: E402

with patch.object(db_mongo, "ensure_indexes"):
    from web.web_app import (  # noqa: E402
        INTERVIEW_TIMELINE_STEPS,
        SIDE_STATUSES,
        application_progress,
        chip_status_counts,
        combine_job_list_query,
        job_matches_status_filter,
        jobs_status_filter_query,
    )

APPLIED_AT = datetime(2026, 3, 10, 14, 30)
INTERVIEW_AT = datetime(2026, 4, 2, 11, 0)


def _job(**overrides):
    job = {
        "status": "applied",
        "applied_at": APPLIED_AT,
        "application_timeline": [
            {"step": "applied", "at": APPLIED_AT},
        ],
        "title": "Software Engineer",
        "company": "Eurowings Digital GmbH",
    }
    job.update(overrides)
    return job


def _eurowings_unsuitable_interview():
    """User applied, marked Unsuitable, then recorded an interview."""
    return _job(
        status="unsuitable",
        application_timeline=[
            {"step": "applied", "at": APPLIED_AT},
            {"step": "interview_1", "at": INTERVIEW_AT},
        ],
    )


class ApplicationProgressTests(unittest.TestCase):
    def test_side_status_interview_is_interview_progress(self):
        job = _eurowings_unsuitable_interview()
        self.assertEqual(application_progress(job), "interview")

    def test_side_status_applied_only_is_applied_progress(self):
        job = _job(status="closed")
        self.assertEqual(application_progress(job), "applied")

    def test_side_status_offer_is_offer_progress(self):
        job = _job(
            status="repost",
            application_timeline=[
                {"step": "applied", "at": APPLIED_AT},
                {"step": "offer", "at": INTERVIEW_AT},
            ],
        )
        self.assertEqual(application_progress(job), "offer")

    def test_side_status_rejected_is_rejected_progress(self):
        job = _job(
            status="duplicate",
            application_timeline=[
                {"step": "applied", "at": APPLIED_AT},
                {"step": "rejected", "at": INTERVIEW_AT},
            ],
        )
        self.assertEqual(application_progress(job), "rejected")

    def test_invite_counts_as_interview(self):
        job = _job(
            status="unsuitable",
            application_timeline=[
                {"step": "applied", "at": APPLIED_AT},
                {"step": "interview_invite", "at": INTERVIEW_AT},
            ],
        )
        self.assertEqual(application_progress(job), "interview")

    def test_side_status_applied_at_without_timeline(self):
        job = _job(status="unsuitable", application_timeline=[])
        self.assertEqual(application_progress(job), "applied")

    def test_flat_interview_status_without_timeline(self):
        job = _job(status="interview", application_timeline=[], applied_at=APPLIED_AT)
        self.assertEqual(application_progress(job), "interview")

    def test_pending_is_not_progress(self):
        job = {"status": "pending", "applied_at": None, "application_timeline": []}
        self.assertIsNone(application_progress(job))

    def test_never_applied_side_status_is_not_progress(self):
        job = {
            "status": "unsuitable",
            "applied_at": None,
            "application_timeline": [],
        }
        self.assertIsNone(application_progress(job))


class StatusFilterMatchTests(unittest.TestCase):
    def test_eurowings_appears_under_interview_and_unsuitable(self):
        job = _eurowings_unsuitable_interview()
        self.assertTrue(job_matches_status_filter(job, "interview"))
        self.assertTrue(job_matches_status_filter(job, "unsuitable"))
        self.assertTrue(job_matches_status_filter(job, "all"))
        self.assertFalse(job_matches_status_filter(job, "applied"))
        self.assertFalse(job_matches_status_filter(job, "offer"))
        self.assertFalse(job_matches_status_filter(job, "pending"))

    def test_side_status_applied_appears_under_applied(self):
        job = _job(status="closed")
        self.assertTrue(job_matches_status_filter(job, "applied"))
        self.assertTrue(job_matches_status_filter(job, "closed"))
        self.assertFalse(job_matches_status_filter(job, "interview"))

    def test_regular_interview_still_matches_interview(self):
        job = _job(
            status="interview",
            application_timeline=[
                {"step": "applied", "at": APPLIED_AT},
                {"step": "interview_2", "at": INTERVIEW_AT},
            ],
        )
        self.assertTrue(job_matches_status_filter(job, "interview"))
        self.assertFalse(job_matches_status_filter(job, "unsuitable"))

    def test_never_applied_unsuitable_only_matches_overlay(self):
        job = {
            "status": "unsuitable",
            "applied_at": None,
            "application_timeline": [],
        }
        self.assertTrue(job_matches_status_filter(job, "unsuitable"))
        self.assertFalse(job_matches_status_filter(job, "interview"))
        self.assertFalse(job_matches_status_filter(job, "applied"))


class StatusFilterQueryTests(unittest.TestCase):
    def test_interview_query_is_not_status_equals_only(self):
        query = jobs_status_filter_query("interview")
        self.assertIn("$or", query)
        self.assertNotEqual(query, {"status": "interview"})
        blob = repr(query)
        self.assertIn("application_timeline.step", blob)
        for step in INTERVIEW_TIMELINE_STEPS:
            self.assertIn(step, blob)

    def test_applied_query_includes_side_status_applied_at(self):
        query = jobs_status_filter_query("applied")
        self.assertIn("$or", query)
        self.assertIn(
            {
                "application_timeline.0": {"$exists": False},
                "applied_at": {"$ne": None},
                "status": {"$in": list(SIDE_STATUSES)},
            },
            query["$or"],
        )

    def test_overlay_and_pending_still_match_status_field(self):
        self.assertEqual(jobs_status_filter_query("unsuitable"), {"status": "unsuitable"})
        self.assertEqual(jobs_status_filter_query("closed"), {"status": "closed"})
        self.assertEqual(jobs_status_filter_query("pending"), {"status": "pending"})
        self.assertEqual(jobs_status_filter_query("all"), {})
        self.assertEqual(jobs_status_filter_query(None), {})

    def test_search_and_progress_filter_combine_with_and(self):
        status_q = jobs_status_filter_query("interview")
        search_q = {
            "$or": [
                {"title": {"$regex": "Eurowings", "$options": "i"}},
                {"company": {"$regex": "Eurowings", "$options": "i"}},
            ]
        }
        combined = combine_job_list_query([status_q, search_q])
        self.assertEqual(combined, {"$and": [status_q, search_q]})


class ChipCountTests(unittest.TestCase):
    def test_side_status_interview_increments_both_chips(self):
        jobs = [
            _eurowings_unsuitable_interview(),
            _job(status="interview", application_timeline=[
                {"step": "applied", "at": APPLIED_AT},
                {"step": "interview_1", "at": INTERVIEW_AT},
            ]),
            _job(status="applied"),
            {"status": "pending", "applied_at": None, "application_timeline": []},
            {
                "status": "unsuitable",
                "applied_at": None,
                "application_timeline": [],
            },
        ]
        counts = chip_status_counts(jobs)
        self.assertEqual(counts.get("interview"), 2)
        self.assertEqual(counts.get("unsuitable"), 2)
        self.assertEqual(counts.get("applied"), 1)
        self.assertEqual(counts.get("pending"), 1)
        self.assertNotIn("all", counts)


if __name__ == "__main__":
    unittest.main()
