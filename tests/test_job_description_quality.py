"""Incomplete JD detection and LinkedIn About-the-job extraction."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

sys.modules.setdefault("playwright", MagicMock())
sys.modules.setdefault("playwright.sync_api", MagicMock())

from core.scraper_utils import (  # noqa: E402
    MIN_COMPLETE_JOB_DESCRIPTION_CHARS,
    assess_job_description,
    is_complete_job_description,
    is_usable_job_description,
)
from scrapers.linkedin_scraper import (  # noqa: E402
    expand_linkedin_see_more,
    extract_linkedin_description,
    linkedin_description_selectors,
)


FULL_JD = (
    "Snke is hiring a Senior Software Engineer - Full Stack in Munich. "
    "You will build TypeScript and React applications on AWS with CDK. "
    "We run product squads, own CI/CD, and ship health-tech features weekly. "
    "Excellent English skills, German is a plus. "
    "Requirements: several years of web engineering, TypeScript, React, AWS. "
    "Nice to have: Terraform and data visualization experience with clinicians."
)


class FakeLocator:
    def __init__(
        self,
        count: int = 0,
        text: str = "",
        html: str = "",
        visible: bool = True,
        children: dict | None = None,
    ):
        self._count = count
        self._text = text
        self._html = html
        self._visible = visible
        self._children = children or {}
        self.clicked = False

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def locator(self, sel):
        return self._children.get(sel, FakeLocator())

    def inner_text(self, timeout=0):
        return self._text

    def inner_html(self, timeout=0):
        return self._html

    def is_visible(self):
        return self._visible

    def click(self, timeout=0):
        self.clicked = True


class FakePage:
    def __init__(self, tree: dict):
        self.tree = tree

    def locator(self, sel):
        return self.tree.get(sel, FakeLocator())


class DescriptionAssessmentTests(unittest.TestCase):
    def test_empty_is_not_usable(self):
        self.assertFalse(is_usable_job_description("  hi  "))
        self.assertEqual(assess_job_description("  hi  ")["kind"], "empty")

    def test_chrome_only_is_incomplete(self):
        text = "About the job\nSee more\nSimilar jobs\nEasy Apply"
        result = assess_job_description(text, title="Engineer", company="Snke")
        self.assertEqual(result["kind"], "incomplete")
        self.assertFalse(is_complete_job_description(text, company="Snke"))

    def test_sidebar_heading_near_start_is_incomplete(self):
        text = "Similar jobs\n" + ("Other Company Engineer Munich\n" * 20)
        result = assess_job_description(
            text,
            title="Senior Software Engineer - Full Stack",
            company="Snke",
            source="linkedin",
        )
        self.assertEqual(result["kind"], "incomplete")

    def test_short_real_text_is_incomplete(self):
        text = "We build web apps with React and TypeScript in Munich."
        self.assertTrue(is_usable_job_description(text))
        self.assertLess(len(text), MIN_COMPLETE_JOB_DESCRIPTION_CHARS)
        self.assertEqual(assess_job_description(text)["kind"], "incomplete")

    def test_full_jd_is_complete(self):
        result = assess_job_description(
            FULL_JD,
            title="Senior Software Engineer - Full Stack",
            company="Snke",
            source="linkedin",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["kind"], "ok")

    def test_company_missing_from_short_linkedin_text(self):
        text = (
            "About the job. Build great products with React. "
            "Similar jobs other firms listed here. " * 8
        )
        result = assess_job_description(
            text,
            title="Senior Software Engineer - Full Stack",
            company="Snke",
            source="linkedin",
        )
        self.assertEqual(result["kind"], "incomplete")


class LinkedInExtractTests(unittest.TestCase):
    def test_selectors_prefer_about_the_job(self):
        sels = linkedin_description_selectors("4478768694")
        self.assertEqual(sels[0], '[componentkey="JobDetails_AboutTheJob_4478768694"]')
        self.assertTrue(any("lazy-column" in s for s in sels) is False)

    @patch("scrapers.linkedin_scraper.pause")
    def test_extract_uses_about_the_job_not_lazy_column(self, _pause):
        about = FakeLocator(count=1, text=FULL_JD)
        lazy = FakeLocator(
            count=1,
            text="About the job\nSee more\nSimilar jobs\nOther Co card",
        )
        screen = FakeLocator(
            count=1,
            children={'"[data-testid="lazy-column"]"': lazy},
        )
        page = FakePage(
            {
                '[componentkey="JobDetails_AboutTheJob_4478768694"]': about,
                '[data-sdui-screen="com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"]': screen,
            }
        )
        text = extract_linkedin_description(page, "4478768694")
        self.assertIn("TypeScript and React", text)
        self.assertNotIn("Similar jobs", text)

    @patch("scrapers.linkedin_scraper.pause")
    def test_see_more_inside_about_the_job_is_clicked(self, _pause):
        see_more = FakeLocator(count=1, visible=True)
        about = FakeLocator(
            count=1,
            text=FULL_JD,
            children={'button:has-text("See more")': see_more},
        )
        page = FakePage(
            {'[componentkey="JobDetails_AboutTheJob_4478768694"]': about}
        )
        self.assertTrue(expand_linkedin_see_more(page, "4478768694"))
        self.assertTrue(see_more.clicked)

    @patch("scrapers.linkedin_scraper.pause")
    def test_trims_similar_jobs_suffix(self, _pause):
        about = FakeLocator(
            count=1,
            text=FULL_JD + "\nSimilar jobs\nAcme Engineer Berlin",
        )
        page = FakePage(
            {'[componentkey="JobDetails_AboutTheJob_4478768694"]': about}
        )
        text = extract_linkedin_description(page, "4478768694")
        self.assertIn("German is a plus", text)
        self.assertNotIn("Acme Engineer", text)


if __name__ == "__main__":
    unittest.main()
