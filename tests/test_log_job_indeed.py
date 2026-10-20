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
        import core.manual_log as ml

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

        with patch.object(ml, "is_job_on_tracker", return_value=False), patch.object(
            ml, "force_save_manual_job", side_effect=fake_save
        ):
            ml.save_link_only_urls([USER_URL], status="applied")

        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["job"]["job_id"], JOB_KEY)
        self.assertEqual(saved[0]["job"]["link"], USER_URL)
        self.assertEqual(saved[0]["status"], "applied")
        self.assertTrue(saved[0]["ai_pending"])

    def test_save_link_only_marks_existing_tracker_applied(self):
        import core.manual_log as ml

        with patch.object(ml, "is_job_on_tracker", return_value=True), patch.object(
            ml, "mark_tracker_job_applied", return_value="applied"
        ) as mark, patch.object(ml, "force_save_manual_job") as save:
            ml.save_link_only_urls([USER_URL], status="applied")

        mark.assert_called_once()
        save.assert_not_called()

    def test_process_urls_falls_back_when_chrome_missing(self):
        import scrapers.manual_apply_scraper as mas
        import core.manual_log as ml

        saved = []

        def fake_save(job, analysis, status="pending", applied_date=None, *, ai_pending=False):
            saved.append(job)
            return True

        fake_pw = MagicMock()
        fake_pw.return_value.__enter__.return_value = MagicMock()
        fake_pw.return_value.__exit__.return_value = False

        with patch.object(mas, "_load_playwright", return_value=(fake_pw, Exception)), patch(
            "core.scraper_utils.connect_browser", side_effect=RuntimeError("no CDP")
        ), patch(
            "matching.ai_matcher.load_user_profile", return_value=""
        ), patch(
            "matching.ai_matcher.load_matching_criteria", return_value=""
        ), patch.object(mas, "get_tracker_job", return_value=None), patch.object(
            ml, "is_job_on_tracker", return_value=False
        ), patch.object(ml, "force_save_manual_job", side_effect=fake_save), patch.object(
            mas, "force_save_manual_job", side_effect=fake_save
        ):
            mas.process_urls([USER_URL], status="applied")

        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["job_id"], JOB_KEY)
        self.assertEqual(saved[0]["source"], "indeed")
        self.assertEqual(saved[0]["link"], USER_URL)

    def test_process_urls_saves_stub_when_scrape_returns_none(self):
        import scrapers.manual_apply_scraper as mas
        import core.manual_log as ml

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

        with patch.object(mas, "_load_playwright", return_value=(fake_pw, Exception)), patch(
            "core.scraper_utils.connect_browser", return_value=browser
        ), patch(
            "matching.ai_matcher.load_user_profile", return_value="profile"
        ), patch(
            "matching.ai_matcher.load_matching_criteria", return_value="criteria"
        ), patch.object(mas, "get_tracker_job", return_value=None), patch.object(
            mas, "scrape_job_page", return_value=None
        ), patch.object(ml, "force_save_manual_job", side_effect=fake_save), patch.object(
            mas, "force_save_manual_job", side_effect=fake_save
        ), patch("core.scraper_utils.pause"):
            mas.process_urls([USER_URL], status="applied")

        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["job"]["job_id"], JOB_KEY)
        self.assertTrue(saved[0]["ai_pending"])
        self.assertEqual(saved[0]["status"], "applied")

    def test_process_urls_marks_existing_as_applied(self):
        import scrapers.manual_apply_scraper as mas
        import core.manual_log as ml

        with patch.object(
            mas, "_load_playwright", side_effect=ImportError("No module named 'playwright'")
        ), patch.object(ml, "is_job_on_tracker", return_value=True), patch.object(
            ml, "mark_tracker_job_applied", return_value="applied"
        ) as mark, patch.object(ml, "force_save_manual_job") as save:
            mas.process_urls([USER_URL], status="applied")

        mark.assert_called_once()
        save.assert_not_called()


class PlaywrightUnavailableTests(unittest.TestCase):
    """Render: Playwright is not installed; Save Link Only must still work."""

    def test_manual_log_module_does_not_import_playwright(self):
        import core.manual_log as ml
        import inspect

        source = inspect.getsource(ml)
        self.assertNotIn("from playwright", source)
        self.assertNotIn("import playwright", source)
        self.assertNotIn("from trafilatura", source)
        self.assertNotIn("import trafilatura", source)
        self.assertNotIn("from scrapers", source)
        self.assertNotIn("import scrapers", source)

    def test_save_link_only_works_when_playwright_is_blocked(self):
        import core.manual_log as ml

        blocked = {
            "playwright": None,
            "playwright.sync_api": None,
            "trafilatura": None,
        }
        saved = []

        def fake_save(job, analysis, status="pending", applied_date=None, *, ai_pending=False):
            saved.append({"job": job, "status": status, "ai_pending": ai_pending})
            return True

        with patch.dict(sys.modules, blocked), patch.object(
            ml, "is_job_on_tracker", return_value=False
        ), patch.object(ml, "force_save_manual_job", side_effect=fake_save):
            ml.save_link_only_urls([USER_URL], status="applied")

        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["job"]["job_id"], JOB_KEY)
        self.assertEqual(saved[0]["status"], "applied")
        self.assertTrue(saved[0]["ai_pending"])

    def test_process_urls_falls_back_when_playwright_missing(self):
        import scrapers.manual_apply_scraper as mas
        import core.manual_log as ml

        saved = []

        def fake_save(job, analysis, status="pending", applied_date=None, *, ai_pending=False):
            saved.append({"job": job, "status": status, "ai_pending": ai_pending})
            return True

        with patch.object(
            mas, "_load_playwright", side_effect=ImportError("No module named 'playwright'")
        ), patch.object(ml, "is_job_on_tracker", return_value=False), patch.object(
            ml, "force_save_manual_job", side_effect=fake_save
        ):
            mas.process_urls([USER_URL], status="applied")

        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["job"]["job_id"], JOB_KEY)
        self.assertEqual(saved[0]["status"], "applied")
        self.assertTrue(saved[0]["ai_pending"])

    def test_importing_scraper_module_does_not_need_playwright(self):
        """Module import must succeed even if playwright is absent from sys.modules."""
        import importlib

        blocked = dict(sys.modules)
        blocked["playwright"] = None
        blocked["playwright.sync_api"] = None
        # Drop a previously-imported scraper so we re-import under the block
        for key in list(blocked):
            if key == "scrapers.manual_apply_scraper" or key.startswith(
                "scrapers.manual_apply_scraper."
            ):
                blocked.pop(key, None)

        with patch.dict(sys.modules, blocked, clear=False):
            sys.modules.pop("scrapers.manual_apply_scraper", None)
            sys.modules["playwright"] = None
            sys.modules["playwright.sync_api"] = None
            mas = importlib.import_module("scrapers.manual_apply_scraper")
            self.assertTrue(hasattr(mas, "save_link_only_urls"))
            self.assertTrue(hasattr(mas, "process_urls"))


if __name__ == "__main__":
    unittest.main()
