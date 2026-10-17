"""Log Job must persist Indeed viewjob?jk= URLs even when scrape/AI fail."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.job_url import (  # noqa: E402
    canonical_job_link,
    detect_source,
    extract_indeed_job_id,
    job_stub_from_url,
    normalize_pasted_job_url,
    plan_manual_log_action,
    resolve_job_id,
)
from core.scraper_utils import extract_indeed_jk_from_url  # noqa: E402

USER_URL = "https://de.indeed.com/viewjob?jk=65ad798c3e603bb0"
JOB_KEY = "65ad798c3e603bb0"


class IndeedUrlParseTests(unittest.TestCase):
    def test_user_viewjob_jk_url(self):
        self.assertEqual(extract_indeed_jk_from_url(USER_URL), JOB_KEY)
        self.assertEqual(extract_indeed_job_id(USER_URL), JOB_KEY)
        self.assertEqual(detect_source(USER_URL), "indeed")
        self.assertEqual(resolve_job_id(USER_URL), JOB_KEY)

    def test_missing_scheme_is_normalised(self):
        raw = "de.indeed.com/viewjob?jk=65ad798c3e603bb0"
        self.assertEqual(
            normalize_pasted_job_url(raw),
            USER_URL,
        )
        self.assertEqual(resolve_job_id(raw), JOB_KEY)

    def test_tracking_params_and_encoding(self):
        messy = (
            "https://de.indeed.com/viewjob?jk=65ad798c3e603bb0"
            "&from=share&utm_source=x"
        )
        self.assertEqual(extract_indeed_job_id(messy), JOB_KEY)
        encoded = "https://de.indeed.com/viewjob?jk%3D65ad798c3e603bb0"
        self.assertEqual(extract_indeed_jk_from_url(encoded), JOB_KEY)

    def test_wrapped_and_trailing_punctuation(self):
        raw = '"https://de.indeed.com/viewjob?jk=65ad798c3e603bb0".'
        self.assertEqual(normalize_pasted_job_url(raw), USER_URL)
        self.assertEqual(resolve_job_id(raw), JOB_KEY)

    def test_serp_vjk_fallback(self):
        serp = "https://de.indeed.com/jobs?q=python&vjk=65ad798c3e603bb0"
        self.assertEqual(extract_indeed_job_id(serp), JOB_KEY)

    def test_canonical_link(self):
        self.assertEqual(canonical_job_link(USER_URL), USER_URL)
        self.assertEqual(
            canonical_job_link("de.indeed.com/viewjob?jk=65ad798c3e603bb0&from=x"),
            USER_URL,
        )

    def test_unparseable_indeed_url_fails_plan(self):
        self.assertIsNone(extract_indeed_job_id("https://de.indeed.com/jobs?q=python"))
        self.assertEqual(plan_manual_log_action(False, "applied", ""), "fail")


class StubAndPlanTests(unittest.TestCase):
    def test_stub_from_user_url(self):
        stub = job_stub_from_url(USER_URL, scrape_warning="Chrome was not available")
        self.assertIsNotNone(stub)
        self.assertEqual(stub["job_id"], JOB_KEY)
        self.assertEqual(stub["source"], "indeed")
        self.assertEqual(stub["link"], USER_URL)
        self.assertIn(JOB_KEY, stub["title"])
        self.assertEqual(stub["scrape_warning"], "Chrome was not available")
        self.assertEqual(stub["description"], "")

    def test_plan_new_url_is_save(self):
        self.assertEqual(plan_manual_log_action(False, "applied", JOB_KEY), "save")
        self.assertEqual(plan_manual_log_action(False, "pending", JOB_KEY), "save")

    def test_plan_existing_tracker_applied_updates(self):
        self.assertEqual(plan_manual_log_action(True, "applied", JOB_KEY), "mark_applied")

    def test_plan_existing_tracker_pending_skips(self):
        self.assertEqual(plan_manual_log_action(True, "pending", JOB_KEY), "skip")


class FallbackSaveTests(unittest.TestCase):
    """Scrape / profile / Chrome failure must still persist the URL."""

    def test_save_link_only_persists_applied_indeed_url(self):
        import scrapers.manual_apply_scraper as mas

        saved = []

        def fake_save(job, analysis, status="pending", applied_date=None, *, ai_pending=False):
            saved.append(
                {
                    "job": job,
                    "status": status,
                    "ai_pending": ai_pending,
                    "applied_at_set": status == "applied",
                }
            )
            return True

        with patch.object(mas, "is_job_on_tracker", return_value=False), patch.object(
            mas, "force_save_manual_job", side_effect=fake_save
        ):
            mas.save_link_only_urls([USER_URL], status="applied")

        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["job"]["job_id"], JOB_KEY)
        self.assertEqual(saved[0]["job"]["link"], USER_URL)
        self.assertEqual(saved[0]["status"], "applied")
        self.assertTrue(saved[0]["ai_pending"])

    def test_save_link_only_marks_existing_tracker_applied(self):
        import scrapers.manual_apply_scraper as mas

        with patch.object(mas, "is_job_on_tracker", return_value=True), patch.object(
            mas, "mark_tracker_job_applied", return_value="applied"
        ) as mark, patch.object(mas, "force_save_manual_job") as save:
            mas.save_link_only_urls([USER_URL], status="applied")

        mark.assert_called_once()
        save.assert_not_called()

    def test_process_urls_falls_back_when_chrome_missing(self):
        import scrapers.manual_apply_scraper as mas

        saved = []

        def fake_save(job, analysis, status="pending", applied_date=None, *, ai_pending=False):
            saved.append(job)
            return True

        fake_pw = MagicMock()
        fake_pw.return_value.__enter__.return_value = MagicMock()
        fake_pw.return_value.__exit__.return_value = False

        with patch.object(mas, "sync_playwright", fake_pw), patch.object(
            mas, "connect_browser", side_effect=RuntimeError("no CDP")
        ), patch.object(mas, "load_user_profile", return_value=""), patch.object(
            mas, "load_matching_criteria", return_value=""
        ), patch.object(mas, "is_job_on_tracker", return_value=False), patch.object(
            mas, "force_save_manual_job", side_effect=fake_save
        ), patch.object(mas, "pause"):
            mas.process_urls([USER_URL], status="applied")

        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["job_id"], JOB_KEY)
        self.assertEqual(saved[0]["source"], "indeed")
        self.assertEqual(saved[0]["link"], USER_URL)

    def test_process_urls_saves_stub_when_scrape_returns_none(self):
        import scrapers.manual_apply_scraper as mas

        saved = []

        def fake_save(job, analysis, status="pending", applied_date=None, *, ai_pending=False):
            saved.append({"job": job, "ai_pending": ai_pending, "status": status})
            return True

        page = MagicMock()
        browser = MagicMock()
        browser.contexts = [MagicMock()]
        browser.contexts[0].pages = [page]
        fake_pw = MagicMock()
        fake_pw.return_value.__enter__.return_value = MagicMock()
        fake_pw.return_value.__exit__.return_value = False

        with patch.object(mas, "sync_playwright", fake_pw), patch.object(
            mas, "connect_browser", return_value=browser
        ), patch.object(mas, "load_user_profile", return_value="profile"), patch.object(
            mas, "load_matching_criteria", return_value="criteria"
        ), patch.object(mas, "is_job_on_tracker", return_value=False), patch.object(
            mas, "scrape_job_page", return_value=None
        ), patch.object(mas, "force_save_manual_job", side_effect=fake_save), patch.object(
            mas, "pause"
        ):
            mas.process_urls([USER_URL], status="applied")

        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["job"]["job_id"], JOB_KEY)
        self.assertTrue(saved[0]["ai_pending"])
        self.assertEqual(saved[0]["status"], "applied")

    def test_process_urls_marks_existing_as_applied(self):
        import scrapers.manual_apply_scraper as mas

        fake_pw = MagicMock()
        fake_pw.return_value.__enter__.return_value = MagicMock()
        fake_pw.return_value.__exit__.return_value = False

        with patch.object(mas, "sync_playwright", fake_pw), patch.object(
            mas, "connect_browser", side_effect=RuntimeError("no CDP")
        ), patch.object(mas, "load_user_profile", return_value=""), patch.object(
            mas, "load_matching_criteria", return_value=""
        ), patch.object(mas, "is_job_on_tracker", return_value=True), patch.object(
            mas, "mark_tracker_job_applied", return_value="applied"
        ) as mark, patch.object(mas, "force_save_manual_job") as save:
            mas.process_urls([USER_URL], status="applied")

        mark.assert_called_once()
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
