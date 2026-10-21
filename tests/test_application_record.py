"""Application record must survive side-status overlays.

Does not touch a live MongoDB — helpers and mocked collection calls only.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import core.db_mongo as db_mongo  # noqa: E402

with patch.object(db_mongo, "ensure_indexes"):
    from web.web_app import (  # noqa: E402
        _hydrate_timeline,
        _status_update_fields,
        _timeline_update_fields,
        SIDE_STATUSES,
    )

from core.db_mongo import (  # noqa: E402
    APPLICATION_QUERY,
    APPLIED_STATUSES,
    count_applied_applications,
    get_applied_job_keys,
    get_applied_location_stats,
    resolve_applied_at,
)

APPLIED_AT = datetime(2026, 3, 10, 14, 30)
INTERVIEW_AT = datetime(2026, 3, 20, 9, 0)
NOW = datetime(2026, 10, 16, 16, 0)


def _applied_job(**overrides):
    job = {
        "status": "applied",
        "applied_at": APPLIED_AT,
        "application_timeline": [
            {"step": "applied", "at": APPLIED_AT},
            {"step": "interview_invite", "at": INTERVIEW_AT},
        ],
        "updated_at": INTERVIEW_AT,
        "matched_at": datetime(2026, 3, 1, 8, 0),
        "location": "Berlin",
    }
    job.update(overrides)
    return job


class StatusUpdateTests(unittest.TestCase):
    def test_side_status_keeps_applied_at_and_timeline(self):
        job = _applied_job()
        for side in SIDE_STATUSES:
            with self.subTest(side=side):
                fields = _status_update_fields(job, side, NOW)
                self.assertEqual(fields["status"], side)
                self.assertNotIn("application_timeline", fields)
                self.assertNotIn("applied_at", fields)

    def test_pending_clears_application_record(self):
        fields = _status_update_fields(_applied_job(), "pending", NOW)
        self.assertEqual(fields["application_timeline"], [])
        self.assertIsNone(fields["applied_at"])

    def test_first_apply_stamps_applied_at_once(self):
        job = {"status": "pending", "applied_at": None, "application_timeline": []}
        fields = _status_update_fields(job, "applied", NOW)
        self.assertEqual(fields["applied_at"], NOW)

    def test_toggle_back_to_applied_does_not_restamp(self):
        job = _applied_job(status="unsuitable")
        fields = _status_update_fields(job, "applied", NOW)
        self.assertNotIn("applied_at", fields)
        self.assertNotIn("application_timeline", fields)

    def test_applied_then_unsuitable_then_toggle_keeps_original_stamp(self):
        job = _applied_job()
        side = _status_update_fields(job, "unsuitable", NOW)
        job = {**job, **side}
        edit = _timeline_update_fields(
            job,
            [
                {"step": "applied", "at": APPLIED_AT},
                {"step": "interview_invite", "at": datetime(2026, 3, 22, 12, 0)},
            ],
            NOW,
        )
        self.assertNotIn("applied_at", edit)
        job = {**job, **edit}
        back = _status_update_fields(job, "applied", NOW)
        self.assertNotIn("applied_at", back)
        self.assertEqual(job["applied_at"], APPLIED_AT)
        self.assertEqual(job["application_timeline"][0]["at"], APPLIED_AT)


class TimelineUpdateTests(unittest.TestCase):
    def test_side_status_edit_keeps_applied_at_status_and_timeline(self):
        job = _applied_job(status="unsuitable")
        edited_invite = datetime(2026, 3, 21, 11, 0)
        incoming = [
            {"step": "applied", "at": APPLIED_AT},
            {"step": "interview_invite", "at": edited_invite},
        ]
        fields = _timeline_update_fields(job, incoming, NOW)
        self.assertEqual(fields["status"], "unsuitable")
        self.assertEqual(fields["application_timeline"], incoming)
        self.assertNotIn("applied_at", fields)

    def test_side_status_undo_does_not_drop_applied_step_or_timestamp(self):
        job = _applied_job(status="closed")
        fields = _timeline_update_fields(job, [], NOW)
        timeline = fields["application_timeline"]
        self.assertEqual(fields["status"], "closed")
        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0]["step"], "applied")
        self.assertEqual(timeline[0]["at"], APPLIED_AT)
        self.assertNotIn("applied_at", fields)

    def test_undo_all_steps_on_applied_job_resets(self):
        job = _applied_job()
        fields = _timeline_update_fields(job, [], NOW)
        self.assertEqual(fields["status"], "pending")
        self.assertEqual(fields["application_timeline"], [])
        self.assertIsNone(fields["applied_at"])

    def test_edit_applied_time_on_active_job_syncs_applied_at(self):
        job = _applied_job()
        new_at = datetime(2026, 3, 11, 10, 0)
        fields = _timeline_update_fields(
            job, [{"step": "applied", "at": new_at}], NOW
        )
        self.assertEqual(fields["status"], "applied")
        self.assertEqual(fields["applied_at"], new_at)

    def test_edit_applied_time_on_side_status_does_not_change_applied_at(self):
        job = _applied_job(status="repost")
        new_at = datetime(2026, 3, 11, 10, 0)
        fields = _timeline_update_fields(
            job, [{"step": "applied", "at": new_at}], NOW
        )
        self.assertEqual(fields["status"], "repost")
        self.assertNotIn("applied_at", fields)
        self.assertEqual(fields["application_timeline"][0]["at"], new_at)


class HydrateTimelineTests(unittest.TestCase):
    def test_side_status_keeps_stored_timeline(self):
        job = _applied_job(status="unsuitable")
        hydrated = _hydrate_timeline(job)
        self.assertEqual(len(hydrated), 2)
        self.assertEqual(hydrated[0]["step"], "applied")

    def test_side_status_seeds_from_applied_at_when_timeline_empty(self):
        job = _applied_job(status="duplicate", application_timeline=[])
        hydrated = _hydrate_timeline(job)
        self.assertEqual(len(hydrated), 1)
        self.assertEqual(hydrated[0]["step"], "applied")
        self.assertEqual(hydrated[0]["at"], APPLIED_AT)

    def test_pending_does_not_invent_progress(self):
        job = {"status": "pending", "applied_at": APPLIED_AT, "application_timeline": []}
        self.assertEqual(_hydrate_timeline(job), [])

    def test_never_applied_side_status_stays_empty(self):
        job = {
            "status": "unsuitable",
            "applied_at": None,
            "application_timeline": [],
        }
        self.assertEqual(_hydrate_timeline(job), [])


class StatsQueryTests(unittest.TestCase):
    def test_application_query_includes_applied_at_and_timeline(self):
        clauses = APPLICATION_QUERY["$or"]
        self.assertIn({"applied_at": {"$ne": None}}, clauses)
        self.assertIn({"status": {"$in": list(APPLIED_STATUSES)}}, clauses)
        self.assertIn({"application_timeline.0": {"$exists": True}}, clauses)

    def test_resolve_applied_at_for_side_status(self):
        job = _applied_job(status="unsuitable")
        self.assertEqual(resolve_applied_at(job), APPLIED_AT)

    def test_count_applied_applications_uses_application_query(self):
        mock_col = MagicMock()
        mock_col.count_documents.return_value = 7
        with patch.object(db_mongo, "get_collection", return_value=mock_col):
            self.assertEqual(count_applied_applications(), 7)
        mock_col.count_documents.assert_called_once_with(APPLICATION_QUERY)

    def test_count_applied_applications_adds_reapplies(self):
        mock_col = MagicMock()
        mock_col.count_documents.return_value = 198
        mock_col.aggregate.return_value = iter([{"n": 1}])
        with patch.object(db_mongo, "get_collection", return_value=mock_col):
            self.assertEqual(count_applied_applications(), 199)

    def test_location_stats_count_reapplies(self):
        mock_col = MagicMock()
        mock_col.find.return_value = [
            {"location": "Berlin, Germany"},
            {
                "location": "Munich, Germany",
                "application_timeline": [
                    {"step": "applied"}, {"step": "rejected"}, {"step": "reapplied"},
                ],
            },
        ]
        with patch.object(db_mongo, "get_collection", return_value=mock_col):
            result = get_applied_location_stats()
        self.assertEqual(result["total"], 3)
        self.assertEqual(sum(row["count"] for row in result["locations"]), 3)

    def test_location_stats_use_application_query(self):
        mock_col = MagicMock()
        mock_col.find.return_value = [{"location": "Berlin, Germany"}]
        with patch.object(db_mongo, "get_collection", return_value=mock_col):
            result = get_applied_location_stats()
        mock_col.find.assert_called_once()
        self.assertEqual(mock_col.find.call_args[0][0], APPLICATION_QUERY)
        self.assertEqual(result["total"], 1)

    def test_applied_job_keys_use_application_query(self):
        mock_db = MagicMock()
        mock_db["matched_jobs"].find.return_value = [
            {"job_id": "abc", "source": "linkedin"},
        ]
        with patch.object(db_mongo, "get_db", return_value=mock_db):
            keys = get_applied_job_keys()
        mock_db["matched_jobs"].find.assert_called_once()
        self.assertEqual(mock_db["matched_jobs"].find.call_args[0][0], APPLICATION_QUERY)
        self.assertEqual(keys, {("abc", "linkedin")})


if __name__ == "__main__":
    unittest.main()
