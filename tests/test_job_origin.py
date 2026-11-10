"""Tracker `origin`: pipeline / manual / agent. Does not touch a live MongoDB."""
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
import core.manual_log as ml  # noqa: E402
from core.job_origin import infer_origin, log_job_origin, origin_filter_query  # noqa: E402

with patch.object(db_mongo, "ensure_indexes"):
    from web.list_query import _jobs_list_query_from_args  # noqa: E402

T = datetime(2026, 9, 18)


class InferOriginTests(unittest.TestCase):
    def test_stored_origin_wins(self):
        self.assertEqual(infer_origin({"origin": "manual", "recommended_by": "claude"}), "manual")

    def test_pipeline_insert_has_no_created_at(self):
        self.assertEqual(infer_origin({"title": "x", "manually_logged": True}), "pipeline")

    def test_log_job_rows(self):
        self.assertEqual(infer_origin({"created_at": T}), "manual")
        self.assertEqual(infer_origin({"created_at": T, "recommended_by": "cursor"}), "agent")

    def test_unmatched_and_timeout_copies_are_pipeline(self):
        self.assertEqual(infer_origin({"created_at": T, "from_unmatched": True}), "pipeline")
        self.assertEqual(infer_origin({"from_timeout": True}), "pipeline")

    def test_agent_stamp_on_pipeline_row_stays_pipeline(self):
        self.assertEqual(infer_origin({"recommended_by": "cursor"}), "pipeline")

    def test_log_job_origin(self):
        self.assertEqual(log_job_origin("gpt"), "agent")
        self.assertEqual(log_job_origin(""), "manual")
        self.assertEqual(log_job_origin("chatgpt"), "manual")


class ForceSaveOriginTests(unittest.TestCase):
    def _save(self, **job_extra):
        matched_col = MagicMock()
        job = {"job_id": "123", "source": "linkedin", "link": "https://x", "title": "Eng", **job_extra}
        with patch.object(ml, "get_collection", return_value=matched_col):
            self.assertTrue(ml.force_save_manual_job(job, ml.stub_ai_pending_analysis()))
        return matched_col.update_one.call_args[0][1]

    def test_manual_origin_only_on_insert(self):
        update = self._save()
        self.assertEqual(update["$setOnInsert"]["origin"], "manual")
        self.assertNotIn("origin", update["$set"])

    def test_agent_origin(self):
        self.assertEqual(self._save(recommended_by="claude")["$setOnInsert"]["origin"], "agent")


class OriginFilterTests(unittest.TestCase):
    def test_filter_query(self):
        self.assertEqual(origin_filter_query("pipeline"), {"origin": "pipeline"})
        self.assertEqual(origin_filter_query("bogus"), {})

    def test_list_query_includes_origin(self):
        query = _jobs_list_query_from_args({"origin": "manual", "country": "all"})
        self.assertIn("'origin': 'manual'", repr(query))
        query = _jobs_list_query_from_args({"origin": "all", "country": "all"})
        self.assertNotIn("origin", repr(query))


if __name__ == "__main__":
    unittest.main()
