"""Log Job recommended_by + Fetch job details (no AI, lazy Playwright).

Does not touch a live MongoDB.
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
import core.manual_log as ml  # noqa: E402

with patch.object(db_mongo, "ensure_indexes"):
    from web.web_app import (  # noqa: E402
        PLAYWRIGHT_UNAVAILABLE_MSG,
        run_fetch_job_details,
    )

USER_URL = "https://de.indeed.com/viewjob?jk=65ad798c3e603bb0"
JOB_KEY = "65ad798c3e603bb0"
APPLIED_AT = datetime(2026, 3, 10, 14, 30)


def _agent_job(**overrides):
    job = {
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
    }
    job.update(overrides)
    return job


class NormalizeRecommendedByTests(unittest.TestCase):
    def test_known_agents(self):
        self.assertEqual(ml.normalize_recommended_by("Claude"), "claude")
        self.assertEqual(ml.normalize_recommended_by("CURSOR"), "cursor")
        self.assertEqual(ml.normalize_recommended_by("gemini"), "gemini")
        self.assertEqual(ml.normalize_recommended_by("gpt"), "gpt")

    def test_none_and_unknown(self):
        self.assertEqual(ml.normalize_recommended_by(""), "")
        self.assertEqual(ml.normalize_recommended_by(None), "")
        self.assertEqual(ml.normalize_recommended_by("none"), "")
        self.assertEqual(ml.normalize_recommended_by("chatgpt"), "")


class SaveLinkRecommendedByTests(unittest.TestCase):
    def test_save_link_only_persists_recommended_by_and_applied(self):
        saved = []

        def fake_save(job, analysis, status="pending", applied_date=None, *, ai_pending=False):
            saved.append({"job": job, "status": status, "ai_pending": ai_pending})
            return True

        with patch.object(ml, "is_job_on_tracker", return_value=False), patch.object(
            ml, "force_save_manual_job", side_effect=fake_save
        ):
            ml.save_link_only_urls(
                [USER_URL], status="applied", recommended_by="cursor"
            )

        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["job"]["recommended_by"], "cursor")
        self.assertEqual(saved[0]["status"], "applied")
        self.assertTrue(saved[0]["ai_pending"])

    def test_save_link_only_without_agent_still_stamps_applied(self):
        saved = []

        def fake_save(job, analysis, status="pending", applied_date=None, *, ai_pending=False):
            saved.append(job)
            return True

        with patch.object(ml, "is_job_on_tracker", return_value=False), patch.object(
            ml, "force_save_manual_job", side_effect=fake_save
        ):
            ml.save_link_only_urls([USER_URL], status="applied")

        self.assertEqual(saved[0]["job_id"], JOB_KEY)
        self.assertNotIn("recommended_by", saved[0])

    def test_force_save_writes_recommended_by(self):
        jobs_col = MagicMock()
        matched_col = MagicMock()

        def get_col(name):
            return jobs_col if name == "jobs" else matched_col

        job = {
            "job_id": JOB_KEY,
            "source": "indeed",
            "link": USER_URL,
            "title": "Engineer",
            "recommended_by": "gpt",
        }
        with patch.object(ml, "get_collection", side_effect=get_col):
            ok = ml.force_save_manual_job(
                job, ml.stub_ai_pending_analysis(), status="applied", ai_pending=True
            )
        self.assertTrue(ok)
        matched_set = matched_col.update_one.call_args[0][1]["$set"]
        self.assertEqual(matched_set["recommended_by"], "gpt")
        self.assertEqual(matched_set["status"], "applied")
        self.assertIn("applied_at", matched_set)
        jobs_set = jobs_col.update_one.call_args[0][1]["$set"]
        self.assertEqual(jobs_set["recommended_by"], "gpt")


class ApplyScrapedDetailsTests(unittest.TestCase):
    def test_patch_excludes_user_progress_fields(self):
        jobs_col = MagicMock()
        matched_col = MagicMock()

        def get_col(name):
            return jobs_col if name == "jobs" else matched_col

        scraped = {
            "job_id": JOB_KEY,
            "source": "indeed",
            "title": "Backend Engineer",
            "company": "Eurowings Digital GmbH",
            "location": "Cologne",
            "description": "Build things.",
            "applicants": "50+",
            "link": USER_URL,
            "status": "pending",
            "applied_at": APPLIED_AT,
            "notes": "wipe me?",
            "recommended_by": "claude",
            "match_score": 9,
        }
        with patch.object(ml, "get_collection", side_effect=get_col):
            result = ml.apply_scraped_details(scraped)

        self.assertTrue(result["ok"])
        set_fields = matched_col.update_one.call_args[0][1]["$set"]
        self.assertEqual(set_fields["title"], "Backend Engineer")
        self.assertEqual(set_fields["company"], "Eurowings Digital GmbH")
        self.assertEqual(set_fields["location"], "Cologne")
        self.assertEqual(set_fields["description"], "Build things.")
        for field in ml.DETAILS_PRESERVE_FIELDS:
            self.assertNotIn(field, set_fields)


class FetchDetailsTests(unittest.TestCase):
    def test_playwright_unavailable_returns_friendly_error(self):
        import scrapers.manual_apply_scraper as mas

        with patch.object(
            mas, "_load_playwright", side_effect=ImportError("No module named 'playwright'")
        ), patch.object(mas, "scrape_job_page") as scrape, patch.object(
            mas, "enrich_job_and_evaluate"
        ) as ai, patch.object(ml, "apply_scraped_details") as save:
            result = mas.fetch_job_details_for_matched_job(_agent_job())

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], PLAYWRIGHT_UNAVAILABLE_MSG)
        scrape.assert_not_called()
        ai.assert_not_called()
        save.assert_not_called()

    def test_fetch_updates_details_without_running_ai(self):
        import scrapers.manual_apply_scraper as mas

        scraped = {
            "job_id": JOB_KEY,
            "source": "indeed",
            "title": "Backend Engineer",
            "company": "Eurowings Digital GmbH",
            "location": "Cologne",
            "description": "Build things.",
            "link": USER_URL,
        }
        page = MagicMock()
        browser = MagicMock()
        browser.contexts = [MagicMock()]
        browser.contexts[0].pages = [page]
        fake_pw = MagicMock()
        fake_pw.return_value.__enter__.return_value = MagicMock()
        fake_pw.return_value.__exit__.return_value = False

        with patch.object(mas, "_load_playwright", return_value=(fake_pw, Exception)), patch(
            "core.scraper_utils.connect_browser", return_value=browser
        ), patch.object(mas, "scrape_job_page", return_value=scraped), patch.object(
            mas, "enrich_job_and_evaluate"
        ) as ai, patch.object(
            mas, "apply_scraped_details", return_value={"ok": True, "error": None}
        ) as save:
            result = mas.fetch_job_details_for_matched_job(_agent_job())

        self.assertTrue(result["ok"])
        self.assertEqual(result["title"], "Backend Engineer")
        ai.assert_not_called()
        save.assert_called_once()

    def test_fetch_rejects_jobs_without_recommended_by(self):
        import scrapers.manual_apply_scraper as mas

        with patch.object(mas, "_load_playwright") as load_pw:
            result = mas.fetch_job_details_for_matched_job(_agent_job(recommended_by=""))
        self.assertFalse(result["ok"])
        self.assertIn("recommended", result["error"].lower())
        load_pw.assert_not_called()

    def test_web_helper_returns_friendly_error_when_scraper_import_fails(self):
        doc = _agent_job()
        with patch.dict(sys.modules, {"scrapers.manual_apply_scraper": None}):
            payload, code = run_fetch_job_details(doc)
        self.assertEqual(code, 503)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], PLAYWRIGHT_UNAVAILABLE_MSG)

    def test_web_helper_rejects_non_agent_jobs(self):
        payload, code = run_fetch_job_details(_agent_job(recommended_by=""))
        self.assertEqual(code, 400)
        self.assertFalse(payload["ok"])


class ProcessUrlsRecommendedByTests(unittest.TestCase):
    def test_stub_fallback_passes_recommended_by(self):
        import scrapers.manual_apply_scraper as mas

        with patch.object(
            mas, "_load_playwright", side_effect=ImportError("No module named 'playwright'")
        ), patch.object(mas, "save_link_only_urls") as save:
            mas.process_urls([USER_URL], status="applied", recommended_by="gemini")

        save.assert_called_once()
        kwargs = save.call_args.kwargs
        self.assertEqual(kwargs.get("recommended_by"), "gemini")
        self.assertEqual(kwargs.get("status"), "applied")


if __name__ == "__main__":
    unittest.main()
