"""Manual LinkedIn scrape uses /jobs/view (not a 24h search-results filter)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scrapers.manual_apply_scraper import (  # noqa: E402
    linkedin_view_url,
    scrape_linkedin_job,
)

LI_ID = "4350206184"
LI_URL = f"https://www.linkedin.com/jobs/view/{LI_ID}/"
VIEW_URL = linkedin_view_url(LI_ID)
JD = (
    "Helsing is a defence AI company. The Autonomous Air System V&V team "
    "builds Software-in-the-Loop environments. You should apply if you have "
    "strong frontend design and coding experience with JavaScript and React."
)

HTML = (ROOT / "templates" / "manual_apply.html").read_text(encoding="utf-8")


class FakeLocator:
    def __init__(self, count: int = 0, text: str = ""):
        self._count = count
        self._text = text

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def inner_text(self, timeout=0):
        return self._text


class FakePage:
    def __init__(self, tree: dict, html: str = ""):
        self.tree = tree
        self.html = html
        self.gotos = []

    def goto(self, url, **kwargs):
        self.gotos.append(url)

    def wait_for_selector(self, sel, timeout=0):
        return True

    def locator(self, sel):
        return self.tree.get(sel, FakeLocator())

    def content(self):
        return self.html


class LinkedInViewUrlTests(unittest.TestCase):
    def test_canonical_view_url(self):
        self.assertEqual(VIEW_URL, LI_URL)
        self.assertNotIn("search-results", VIEW_URL)
        self.assertNotIn("f_TPR", VIEW_URL)


class ScrapeLinkedInJobTests(unittest.TestCase):
    def _page(self, **tree):
        defaults = {
            ".job-details-jobs-unified-top-card__job-title": FakeLocator(
                1, "Software Engineer - Frontend Autonomous Air System V&V"
            ),
            ".job-details-jobs-unified-top-card__company-name a": FakeLocator(
                1, "Helsing"
            ),
            ".job-details-jobs-unified-top-card__bullet": FakeLocator(
                1, "Munich, Bavaria, Germany"
            ),
        }
        defaults.update(tree)
        return FakePage(defaults)

    def test_navigates_to_view_url_not_search_results(self):
        page = self._page()
        with patch("scrapers.manual_apply_scraper._su") as su, patch(
            "scrapers.linkedin_scraper.extract_linkedin_description", return_value=JD
        ), patch("scrapers.manual_apply_scraper.extract_linkedin_job_id", return_value=LI_ID):
            su.return_value.pause = lambda *a, **k: None
            su.return_value.safe_text = lambda loc, timeout=5000: loc._text
            job = scrape_linkedin_job(page, LI_URL)

        self.assertEqual(page.gotos, [VIEW_URL])
        self.assertTrue(job["description"])
        self.assertEqual(job["title"], "Software Engineer - Frontend Autonomous Air System V&V")
        self.assertEqual(job["company"], "Helsing")
        self.assertEqual(job["location"], "Munich, Bavaria, Germany")
        self.assertEqual(job["job_id"], LI_ID)
        self.assertEqual(job["source"], "linkedin")
        self.assertEqual(job["link"], VIEW_URL)

    def test_search_results_paste_still_opens_view(self):
        page = self._page()
        search = (
            f"https://www.linkedin.com/jobs/search-results/"
            f"?currentJobId={LI_ID}&keywords=frontend&f_TPR=r86400"
        )
        with patch("scrapers.manual_apply_scraper._su") as su, patch(
            "scrapers.linkedin_scraper.extract_linkedin_description", return_value=JD
        ), patch("scrapers.manual_apply_scraper.extract_linkedin_job_id", return_value=LI_ID):
            su.return_value.pause = lambda *a, **k: None
            su.return_value.safe_text = lambda loc, timeout=5000: loc._text
            scrape_linkedin_job(page, search)

        self.assertEqual(page.gotos, [VIEW_URL])

    def test_timeout_still_extracts_public_markup(self):
        page = FakePage(
            {
                "h1": FakeLocator(1, "Software Engineer - Frontend Autonomous Air System V&V"),
                ".topcard__org-name-link": FakeLocator(1, "Helsing"),
                ".topcard__flavor--bullet": FakeLocator(1, "Munich, Bavaria, Germany"),
            }
        )

        def boom(*args, **kwargs):
            raise TimeoutError("wait")

        page.wait_for_selector = boom
        with patch("scrapers.manual_apply_scraper._su") as su, patch(
            "scrapers.linkedin_scraper.extract_linkedin_description",
            return_value="",
        ), patch(
            "scrapers.manual_apply_scraper._extract_main_text_from_html",
            return_value=JD,
        ), patch("scrapers.manual_apply_scraper.extract_linkedin_job_id", return_value=LI_ID):
            su.return_value.pause = lambda *a, **k: None
            su.return_value.safe_text = lambda loc, timeout=5000: loc._text
            job = scrape_linkedin_job(page, LI_URL)

        self.assertEqual(job["title"], "Software Engineer - Frontend Autonomous Air System V&V")
        self.assertEqual(job["company"], "Helsing")
        self.assertIn("Helsing is a defence AI company", job["description"])

    def test_empty_page_returns_warning_not_none(self):
        page = FakePage({})
        with patch("scrapers.manual_apply_scraper._su") as su, patch(
            "scrapers.linkedin_scraper.extract_linkedin_description",
            return_value="",
        ), patch(
            "scrapers.manual_apply_scraper._extract_main_text_from_html",
            return_value="",
        ), patch(
            "scrapers.manual_apply_scraper._fallback_page_text",
            return_value="",
        ), patch("scrapers.manual_apply_scraper.extract_linkedin_job_id", return_value=LI_ID):
            su.return_value.pause = lambda *a, **k: None
            su.return_value.safe_text = lambda loc, timeout=5000: ""
            job = scrape_linkedin_job(page, LI_URL)

        self.assertIsNotNone(job)
        self.assertEqual(job["description"], "")
        self.assertIn("empty", job["scrape_warning"].lower())


class ProcessUrlsRefreshStubTests(unittest.TestCase):
    def test_existing_link_only_is_scraped_in_place(self):
        import scrapers.manual_apply_scraper as mas

        existing = {
            "job_id": LI_ID,
            "source": "linkedin",
            "title": f"(Link only) LinkedIn {LI_ID}",
            "description": "",
            "ai_pending": True,
            "notes": "keep me",
        }
        scraped = {
            "job_id": LI_ID,
            "source": "linkedin",
            "title": "Software Engineer - Frontend",
            "company": "Helsing",
            "location": "Munich",
            "description": JD,
            "link": LI_URL,
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
        ), patch(
            "matching.ai_matcher.load_user_profile", return_value="profile"
        ), patch(
            "matching.ai_matcher.load_matching_criteria", return_value="criteria"
        ), patch.object(mas, "get_tracker_job", return_value=existing), patch.object(
            mas, "scrape_job_page", return_value=scraped
        ), patch.object(
            mas, "enrich_job_and_evaluate", return_value={"match_score": 8.5}
        ) as enrich, patch.object(
            mas, "apply_analysis_to_existing", return_value=True
        ) as apply_ai, patch.object(
            mas, "force_save_manual_job"
        ) as force, patch.object(
            mas, "save_job_record"
        ) as save_rec, patch("core.scraper_utils.pause"):
            mas.process_urls([LI_URL], status="pending")

        enrich.assert_called_once()
        apply_ai.assert_called_once()
        force.assert_not_called()
        save_rec.assert_not_called()

    def test_existing_complete_card_is_still_skipped(self):
        import scrapers.manual_apply_scraper as mas

        existing = {
            "job_id": LI_ID,
            "source": "linkedin",
            "title": "Already scraped",
            "description": JD,
            "ai_pending": False,
            "summary": "Matched",
            "details_fetched_at": True,
        }
        fake_pw = MagicMock()
        fake_pw.return_value.__enter__.return_value = MagicMock()
        fake_pw.return_value.__exit__.return_value = False

        with patch.object(mas, "_load_playwright", return_value=(fake_pw, Exception)), patch(
            "core.scraper_utils.connect_browser", return_value=MagicMock()
        ), patch(
            "matching.ai_matcher.load_user_profile", return_value="profile"
        ), patch(
            "matching.ai_matcher.load_matching_criteria", return_value="criteria"
        ), patch.object(mas, "get_tracker_job", return_value=existing), patch.object(
            mas, "handle_existing_tracker", return_value="skip"
        ) as handle, patch.object(mas, "scrape_job_page") as scrape:
            mas.process_urls([LI_URL], status="pending")

        handle.assert_called_once()
        scrape.assert_not_called()


class FetchAndRunAiEmptyScrapeTests(unittest.TestCase):
    def test_fetch_rejects_empty_description(self):
        import scrapers.manual_apply_scraper as mas

        stub = {
            "job_id": LI_ID,
            "source": "linkedin",
            "link": LI_URL,
            "title": f"(Link only) LinkedIn {LI_ID}",
            "ai_pending": True,
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
        ), patch.object(
            mas, "scrape_job_page",
            return_value={"description": "", "scrape_warning": "login wall"},
        ), patch.object(mas, "apply_scraped_details") as save:
            result = mas.fetch_job_details_for_matched_job(stub)

        self.assertFalse(result["ok"])
        self.assertIn("login wall", result["error"])
        save.assert_not_called()

    def test_fetch_infers_missing_identity(self):
        import scrapers.manual_apply_scraper as mas

        stub = {
            "job_id": LI_ID,
            "source": "linkedin",
            "link": LI_URL,
            "title": f"(Link only) LinkedIn {LI_ID}",
            "ai_pending": True,
        }
        scraped = {
            "job_id": LI_ID,
            "source": "linkedin",
            "title": "",
            "company": "",
            "location": "",
            "description": JD,
            "link": LI_URL,
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
            mas, "infer_job_metadata_with_ai",
            return_value={
                "title": "Software Engineer - Frontend",
                "company": "Helsing",
                "location": "Munich",
            },
        ), patch.object(
            mas, "apply_scraped_details", return_value={"ok": True, "error": None}
        ) as save:
            result = mas.fetch_job_details_for_matched_job(stub)

        self.assertTrue(result["ok"])
        written = save.call_args[0][0]
        self.assertEqual(written["title"], "Software Engineer - Frontend")
        self.assertEqual(written["company"], "Helsing")
        self.assertEqual(written["location"], "Munich")


class LogJobUiRefreshTests(unittest.TestCase):
    def test_submit_includes_tracker_cards_that_need_scrape(self):
        self.assertIn("trackerToRefresh", HTML)
        self.assertIn("needs_scrape", HTML)
        self.assertIn("details_missing", HTML)

    def test_paste_does_not_auto_open_link_only_cards(self):
        self.assertIn("Already on Tracker without details", HTML)
        self.assertIn("use <strong>Scrape &amp; Log</strong>", HTML)


if __name__ == "__main__":
    unittest.main()
