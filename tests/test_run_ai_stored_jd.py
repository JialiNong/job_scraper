"""Run AI uses a complete stored JD directly and skips Chrome."""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import scrapers.manual_apply_scraper as mas  # noqa: E402

HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

BODY = (
    "You will own features end to end across a TypeScript and Node.js backend "
    "and a React frontend, ship to production daily, and work closely with "
    "product and design. Requirements: 5+ years building web products, strong "
    "TypeScript, Node.js and PostgreSQL experience, comfort with cloud "
    "infrastructure and CI/CD, and a pragmatic product mindset. "
)


def _doc(**overrides):
    doc = {
        "_id": "aaaaaaaaaaaaaaaaaaaaaaaa",
        "job_id": "4300000001",
        "source": "linkedin",
        "link": "https://www.linkedin.com/jobs/view/4300000001/",
        "title": "Senior Full-Stack Engineer (m/f/d)",
        "company": "Buying Labs GmbH",
        "location": "Hamburg",
        "description": (
            "About the role: Senior Full-Stack Engineer at Buying Labs GmbH. "
            + BODY * 3
            + "Fluent English required; German is a plus."
        ),
        "notes": "keep me",
    }
    doc.update(overrides)
    return doc


def _fake_ai_matcher():
    mod = types.ModuleType("matching.ai_matcher")
    mod.load_user_profile = lambda: "profile"
    mod.load_matching_criteria = lambda: "criteria"
    return patch.dict(sys.modules, {"matching.ai_matcher": mod})


class StoredJdUsableTests(unittest.TestCase):
    def test_complete_jd_is_usable(self):
        self.assertTrue(mas.stored_jd_usable(_doc()))

    def test_link_only_stub_is_not_usable(self):
        self.assertFalse(mas.stored_jd_usable(_doc(title="(Link only) LinkedIn job")))

    def test_short_jd_is_not_usable(self):
        self.assertFalse(mas.stored_jd_usable(_doc(description="Short teaser text.")))

    def test_empty_jd_is_not_usable(self):
        self.assertFalse(mas.stored_jd_usable(_doc(description="")))


class RunAiStoredJdTests(unittest.TestCase):
    def test_complete_jd_skips_chrome_and_saves(self):
        analysis = {"match_score": 8.0, "match_gate": "scored"}
        with _fake_ai_matcher(), \
                patch.object(mas, "_load_playwright") as load_pw, \
                patch.object(mas, "enrich_job_and_evaluate", return_value=analysis) as evaluate, \
                patch.object(mas, "apply_analysis_to_existing", return_value=True) as save:
            result = mas.run_ai_on_matched_job(_doc())

        load_pw.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertTrue(result["used_stored_jd"])
        self.assertEqual(result["match_score"], 8.0)
        job = evaluate.call_args[0][0]
        self.assertIn("German is a plus", job["description"])
        saved_job = save.call_args[0][0]
        self.assertEqual(saved_job["job_id"], "4300000001")
        self.assertEqual(saved_job["source"], "linkedin")

    def test_gate_stop_is_reported(self):
        analysis = {
            "match_score": 1.0,
            "match_gate": "german",
            "disqualification_reason": "Mandatory German",
        }
        with _fake_ai_matcher(), \
                patch.object(mas, "_load_playwright") as load_pw, \
                patch.object(mas, "enrich_job_and_evaluate", return_value=analysis), \
                patch.object(mas, "apply_analysis_to_existing", return_value=True):
            result = mas.run_ai_on_matched_job(_doc())
        load_pw.assert_not_called()
        self.assertEqual(result["match_gate"], "german")
        self.assertEqual(result["gate_reason"], "Mandatory German")

    def test_link_only_still_scrapes(self):
        scraped = dict(_doc(), job_id="4300000001")
        page = MagicMock()
        browser = MagicMock()
        browser.contexts = [MagicMock(pages=[page])]
        pw_cm = MagicMock()
        pw_cm.__enter__.return_value = MagicMock()
        sync_playwright = MagicMock(return_value=pw_cm)
        fake_utils = types.SimpleNamespace(connect_browser=lambda pw, label: browser)
        with _fake_ai_matcher(), \
                patch.object(mas, "_load_playwright", return_value=(sync_playwright, None)), \
                patch.dict(sys.modules, {"core.scraper_utils": __import__("core.scraper_utils", fromlist=["x"])}), \
                patch("core.scraper_utils.connect_browser", fake_utils.connect_browser, create=True), \
                patch.object(mas, "scrape_job_page", return_value=scraped) as scrape, \
                patch.object(mas, "enrich_job_and_evaluate", return_value={"match_score": 7.0}), \
                patch.object(mas, "apply_analysis_to_existing", return_value=True):
            result = mas.run_ai_on_matched_job(
                _doc(title="(Link only) LinkedIn job", description="")
            )
        scrape.assert_called_once()
        self.assertTrue(result["ok"])
        self.assertFalse(result["used_stored_jd"])


class RunAiUiTests(unittest.TestCase):
    def test_toast_mentions_stored_jd(self):
        self.assertIn("using the stored JD (no Chrome)", HTML)
        self.assertIn("startData.uses_stored_jd", HTML)


if __name__ == "__main__":
    unittest.main()
