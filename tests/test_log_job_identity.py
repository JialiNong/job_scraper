"""Log Job optional title / company / location (Save Link Only, no Playwright)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import core.manual_log as ml  # noqa: E402

USER_URL = "https://de.indeed.com/viewjob?jk=65ad798c3e603bb0"
JOB_KEY = "65ad798c3e603bb0"


class NormalizeIdentityTests(unittest.TestCase):
    def test_strips_and_records_typed_fields(self):
        ident = ml.normalize_manual_identity(
            title="  Senior Engineer  ",
            company="Eurowings Digital GmbH",
            location="",
        )
        self.assertEqual(ident["values"]["title"], "Senior Engineer")
        self.assertEqual(ident["values"]["company"], "Eurowings Digital GmbH")
        self.assertNotIn("location", ident["values"])
        self.assertEqual(ident["manual_fields"], ("title", "company"))

    def test_empty_is_noop(self):
        ident = ml.normalize_manual_identity(title="  ", company=None, location=None)
        self.assertEqual(ident["values"], {})
        self.assertEqual(ident["manual_fields"], ())

    def test_truncates_long_title(self):
        ident = ml.normalize_manual_identity(title="x" * 500)
        self.assertEqual(len(ident["values"]["title"]), 200)


class KeepManualIdentityTests(unittest.TestCase):
    def test_user_typed_fields_win_over_scrape(self):
        scraped = {
            "title": "Scraped title",
            "company": "Scraped Co",
            "location": "Munich",
        }
        existing = {
            "title": "User title",
            "company": "User Co",
            "location": "Berlin",
            "manual_fields": ["title", "company", "location"],
        }
        ml.keep_manual_identity(scraped, existing)
        self.assertEqual(scraped["title"], "User title")
        self.assertEqual(scraped["company"], "User Co")
        self.assertEqual(scraped["location"], "Berlin")

    def test_empty_user_field_allows_scrape(self):
        scraped = {"title": "Scraped title", "company": "Scraped Co", "location": "Munich"}
        existing = {
            "title": "User title",
            "company": "",
            "location": "",
            "manual_fields": ["title"],
        }
        ml.keep_manual_identity(scraped, existing)
        self.assertEqual(scraped["title"], "User title")
        self.assertEqual(scraped["company"], "Scraped Co")
        self.assertEqual(scraped["location"], "Munich")

    def test_blank_scrape_keeps_non_stub_existing(self):
        scraped = {"title": "", "company": "", "location": ""}
        existing = {
            "title": "(Link only) Indeed abc",
            "company": "Existing Co",
            "location": "Cologne",
            "manual_fields": [],
        }
        ml.keep_manual_identity(scraped, existing)
        self.assertEqual(scraped["title"], "")
        self.assertEqual(scraped["company"], "Existing Co")
        self.assertEqual(scraped["location"], "Cologne")


class SaveLinkIdentityTests(unittest.TestCase):
    def test_save_link_only_writes_user_identity_not_stub_title(self):
        saved = []

        def fake_save(job, analysis, status="pending", applied_date=None, *, ai_pending=False):
            saved.append(job)
            return True

        with patch.object(ml, "is_job_on_tracker", return_value=False), patch.object(
            ml, "force_save_manual_job", side_effect=fake_save
        ):
            ml.save_link_only_urls(
                [USER_URL],
                status="applied",
                title="Backend Engineer",
                company="Eurowings Digital GmbH",
                location="Cologne, Germany",
            )

        self.assertEqual(len(saved), 1)
        job = saved[0]
        self.assertEqual(job["title"], "Backend Engineer")
        self.assertEqual(job["company"], "Eurowings Digital GmbH")
        self.assertEqual(job["location"], "Cologne, Germany")
        self.assertEqual(job["job_id"], JOB_KEY)
        self.assertCountEqual(job["manual_fields"], ["title", "company", "location"])
        self.assertFalse(str(job["title"]).startswith("(Link only)"))

    def test_save_link_only_without_identity_still_uses_stub_title(self):
        saved = []

        def fake_save(job, analysis, status="pending", applied_date=None, *, ai_pending=False):
            saved.append(job)
            return True

        with patch.object(ml, "is_job_on_tracker", return_value=False), patch.object(
            ml, "force_save_manual_job", side_effect=fake_save
        ):
            ml.save_link_only_urls([USER_URL], status="applied")

        self.assertTrue(saved[0]["title"].startswith("(Link only)"))
        self.assertEqual(saved[0].get("company"), "")
        self.assertNotIn("manual_fields", saved[0])

    def test_identity_works_when_playwright_is_blocked(self):
        saved = []

        def fake_save(job, analysis, status="pending", applied_date=None, *, ai_pending=False):
            saved.append(job)
            return True

        blocked = {"playwright": None, "playwright.sync_api": None}
        with patch.dict(sys.modules, blocked), patch.object(
            ml, "is_job_on_tracker", return_value=False
        ), patch.object(ml, "force_save_manual_job", side_effect=fake_save):
            ml.save_link_only_urls(
                [USER_URL], status="applied", title="From phone", company="Acme"
            )

        self.assertEqual(saved[0]["title"], "From phone")
        self.assertEqual(saved[0]["company"], "Acme")

    def test_force_save_persists_identity_fields(self):
        jobs_col = MagicMock()
        matched_col = MagicMock()

        def get_col(name):
            return jobs_col if name == "jobs" else matched_col

        job = {
            "job_id": JOB_KEY,
            "source": "indeed",
            "link": USER_URL,
            "title": "Backend Engineer",
            "company": "Eurowings Digital GmbH",
            "location": "Cologne",
            "manual_fields": ["title", "company", "location"],
        }
        with patch.object(ml, "get_collection", side_effect=get_col):
            ok = ml.force_save_manual_job(
                job, ml.stub_ai_pending_analysis(), status="applied", ai_pending=True
            )
        self.assertTrue(ok)
        matched_set = matched_col.update_one.call_args[0][1]["$set"]
        self.assertEqual(matched_set["title"], "Backend Engineer")
        self.assertEqual(matched_set["company"], "Eurowings Digital GmbH")
        self.assertEqual(matched_set["location"], "Cologne")
        self.assertEqual(matched_set["manual_fields"], ["title", "company", "location"])
        self.assertIn("applied_at", matched_set)


class FetchKeepsManualIdentityTests(unittest.TestCase):
    def test_fetch_details_does_not_overwrite_user_title(self):
        import scrapers.manual_apply_scraper as mas

        existing = {
            "job_id": JOB_KEY,
            "source": "indeed",
            "link": USER_URL,
            "title": "User title",
            "company": "User Co",
            "location": "Berlin",
            "recommended_by": "cursor",
            "manual_fields": ["title", "company", "location"],
        }
        scraped = {
            "job_id": JOB_KEY,
            "source": "indeed",
            "title": "Scraped title",
            "company": "Scraped Co",
            "location": "Munich",
            "description": "A JD",
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
            mas, "apply_scraped_details", return_value={"ok": True, "error": None}
        ) as save:
            result = mas.fetch_job_details_for_matched_job(existing)

        self.assertTrue(result["ok"])
        written = save.call_args[0][0]
        self.assertEqual(written["title"], "User title")
        self.assertEqual(written["company"], "User Co")
        self.assertEqual(written["location"], "Berlin")
        self.assertEqual(written["description"], "A JD")


class ProcessUrlsIdentityFallbackTests(unittest.TestCase):
    def test_playwright_missing_passes_identity_to_stub_save(self):
        import scrapers.manual_apply_scraper as mas

        ident = ml.normalize_manual_identity(title="Phone title", location="Leipzig")
        with patch.object(
            mas, "_load_playwright", side_effect=ImportError("No module named 'playwright'")
        ), patch.object(mas, "save_link_only_urls") as save:
            mas.process_urls([USER_URL], status="applied", identity=ident)

        save.assert_called_once()
        self.assertEqual(save.call_args.kwargs.get("identity"), ident)
        self.assertEqual(save.call_args.kwargs.get("status"), "applied")


if __name__ == "__main__":
    unittest.main()
