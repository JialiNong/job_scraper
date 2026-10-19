"""Batch Fetch job details (scrape only, sequential, Render-safe).

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
import core.manual_log as ml  # noqa: E402

with patch.object(db_mongo, "ensure_indexes"):
    from web.web_app import (  # noqa: E402
        PLAYWRIGHT_UNAVAILABLE_MSG,
        _serialize,
        guard_fetch_details_playwright,
        run_fetch_details_batch_jobs,
    )

HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

USER_URL = "https://de.indeed.com/viewjob?jk=65ad798c3e603bb0"
JOB_KEY = "65ad798c3e603bb0"
APPLIED_AT = datetime(2026, 3, 10, 14, 30)


def _agent_job(**overrides):
    job = {
        "_id": "aaaaaaaaaaaaaaaaaaaaaaaa",
        "job_id": JOB_KEY,
        "source": "indeed",
        "link": USER_URL,
        "title": "(Link only) Indeed job",
        "company": "",
        "location": "",
        "description": "",
        "recommended_by": "cursor",
        "status": "applied",
        "applied_at": APPLIED_AT,
        "application_timeline": [{"step": "applied", "at": APPLIED_AT}],
        "notes": "keep me",
        "ai_pending": True,
    }
    job.update(overrides)
    return job


class JobMissingDetailsTests(unittest.TestCase):
    def test_link_only_title_is_missing(self):
        self.assertTrue(ml.job_missing_details(_agent_job()))

    def test_fetched_at_means_present(self):
        job = _agent_job(
            title="Backend Engineer",
            details_fetched_at=datetime(2026, 10, 19),
            ai_pending=True,
            summary="",
        )
        self.assertFalse(ml.job_missing_details(job))

    def test_empty_description_is_missing(self):
        job = _agent_job(title="Staff Engineer", description="", ai_pending=False)
        self.assertTrue(ml.job_missing_details(job))

    def test_list_payload_without_description_uses_ai_pending(self):
        job = {
            "title": "Staff Engineer",
            "ai_pending": True,
            "recommended_by": "gpt",
        }
        self.assertTrue(ml.job_missing_details(job))

    def test_non_agent_serialize_is_not_missing(self):
        job = _agent_job(recommended_by="", title="Normal match", summary="hi")
        job.pop("description", None)
        serialized = _serialize(job)
        self.assertEqual(serialized["recommended_by"], "")
        self.assertFalse(serialized["details_missing"])


class GuardPlaywrightTests(unittest.TestCase):
    def test_import_error_returns_friendly_503(self):
        with patch.dict(sys.modules, {"scrapers.manual_apply_scraper": None}):
            payload, code = guard_fetch_details_playwright()
        self.assertEqual(code, 503)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], PLAYWRIGHT_UNAVAILABLE_MSG)

    def test_load_playwright_import_error(self):
        import scrapers.manual_apply_scraper as mas

        with patch.object(
            mas, "_load_playwright", side_effect=ImportError("No module named 'playwright'")
        ):
            payload, code = guard_fetch_details_playwright()
        self.assertEqual(code, 503)
        self.assertEqual(payload["error"], PLAYWRIGHT_UNAVAILABLE_MSG)


class BatchRunTests(unittest.TestCase):
    def test_continues_after_one_failure_and_skips_non_agent(self):
        calls = []

        def fake_fetch(doc):
            calls.append(doc["job_id"])
            if doc["job_id"] == "b":
                return {"ok": False, "error": "Scraping failed"}, 502
            return {"ok": True, "title": doc["title"], "error": None}, 200

        docs = [
            _agent_job(_id="1", job_id="a", title="Job A"),
            _agent_job(_id="2", job_id="b", title="Job B"),
            _agent_job(_id="3", job_id="c", title="Job C", recommended_by=""),
            _agent_job(_id="4", job_id="d", title="Job D"),
        ]
        summary = run_fetch_details_batch_jobs(docs, fetch_one=fake_fetch)

        self.assertEqual(calls, ["a", "b", "d"])
        self.assertEqual(summary["done_count"], 2)
        self.assertEqual(summary["fail_count"], 1)
        self.assertEqual(summary["skip_count"], 1)
        self.assertEqual(len(summary["results"]), 4)
        self.assertTrue(summary["results"][0]["ok"])
        self.assertFalse(summary["results"][1]["ok"])
        self.assertIn("recommended", summary["results"][2]["error"].lower())
        self.assertTrue(summary["results"][3]["ok"])

    def test_does_not_call_ai(self):
        import scrapers.manual_apply_scraper as mas

        with patch.object(
            mas, "fetch_job_details_for_matched_job", return_value={"ok": True, "title": "X"}
        ) as fetch, patch.object(mas, "enrich_job_and_evaluate") as ai:
            summary = run_fetch_details_batch_jobs(
                [_agent_job()],
                fetch_one=lambda doc: mas.fetch_job_details_for_matched_job(doc),
            )
        fetch.assert_called_once()
        ai.assert_not_called()
        self.assertEqual(summary["done_count"], 1)


class TrackerUiContractTests(unittest.TestCase):
    def test_batch_controls_and_endpoints_exist(self):
        self.assertIn('id="selectVisibleBtn"', HTML)
        self.assertIn('id="selectMissingBtn"', HTML)
        self.assertIn('id="fetchSelectedBtn"', HTML)
        self.assertIn("function fetchDetailsForSelected()", HTML)
        self.assertIn("function selectMissingDetails()", HTML)
        self.assertIn("/api/fetch-details/batch", HTML)
        self.assertIn("/api/fetch-details/ids", HTML)
        self.assertIn('class="card-select"', HTML)
        self.assertIn("Notification.requestPermission", HTML)
        self.assertRegex(HTML, r"white-space:\s*nowrap")

    def test_select_missing_uses_current_filters(self):
        self.assertIn("currentJobListParams()", HTML)
        self.assertIn("params.set('missing', '1')", HTML)


if __name__ == "__main__":
    unittest.main()
