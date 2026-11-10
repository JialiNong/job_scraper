"""Posting key: one id per job ad across URL decorations and sources."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.job_url import posting_key, posting_key_fields  # noqa: E402


class PostingKeyTest(unittest.TestCase):
    def test_personio_language_param_matches_plain_link(self):
        plain = posting_key("https://bookboost.jobs.personio.de/job/2811207")
        self.assertEqual(plain, "personio:bookboost:2811207")
        self.assertEqual(posting_key("https://bookboost.jobs.personio.de/job/2811207?language=en"), plain)
        self.assertEqual(posting_key("bookboost.jobs.personio.com/job/2811207/?EM=English#apply"), plain)

    def test_personio_slug_kept(self):
        self.assertNotEqual(
            posting_key("https://a.jobs.personio.de/job/1"),
            posting_key("https://b.jobs.personio.de/job/1"),
        )

    def test_greenhouse_board_and_careers_page(self):
        board = posting_key("https://job-boards.eu.greenhouse.io/acme/jobs/4012345?gh_src=abc")
        self.assertEqual(board, "greenhouse:4012345")
        self.assertEqual(posting_key("https://acme.com/careers/job?gh_jid=4012345&utm_source=x"), board)

    def test_lever_and_ashby(self):
        uid = "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"
        self.assertEqual(posting_key(f"https://jobs.eu.lever.co/acme/{uid}/apply?lever-source=LinkedIn"), f"lever:{uid}")
        self.assertEqual(posting_key(f"https://jobs.ashbyhq.com/acme/{uid}/application"), f"ashby:{uid}")
        self.assertEqual(posting_key(f"https://acme.com/jobs?ashby_jid={uid}"), f"ashby:{uid}")

    def test_workable_recruitee_smartrecruiters_join(self):
        self.assertEqual(posting_key("https://apply.workable.com/acme/j/AB12CD34EF/"), "workable:ab12cd34ef")
        self.assertEqual(posting_key("https://acme.recruitee.com/o/senior-dev?lang=en"), "recruitee:acme:senior-dev")
        self.assertEqual(posting_key("https://jobs.smartrecruiters.com/Acme/7440000000-fullstack"), "smartrecruiters:7440000000")
        self.assertEqual(posting_key("https://join.com/companies/acme/12345678-dev?pid=1"), "join:12345678")

    def test_workday_ignores_language_segment(self):
        a = posting_key("https://acme.wd3.myworkdayjobs.com/en-US/External/job/Munich/Full-Stack-Dev_R12345")
        b = posting_key("https://acme.wd3.myworkdayjobs.com/de-DE/External/job/Munich/Full-Stack-Entwickler_R12345")
        self.assertEqual(a, "workday:acme:r12345")
        self.assertEqual(a, b)

    def test_generic_site_drops_noise_params_only(self):
        base = posting_key("https://careers.acme.de/jobs/fullstack")
        self.assertEqual(base, "url:careers.acme.de/jobs/fullstack")
        self.assertEqual(posting_key("https://www.careers.acme.de/jobs/fullstack/?lang=en&utm_source=li"), base)
        self.assertNotEqual(
            posting_key("https://acme.de/job?id=1"), posting_key("https://acme.de/job?id=2")
        )

    def test_linkedin_indeed_have_no_key(self):
        self.assertIsNone(posting_key("https://www.linkedin.com/jobs/view/4361502751/"))
        self.assertIsNone(posting_key("https://de.indeed.com/viewjob?jk=abc123"))
        self.assertEqual(posting_key_fields("https://de.indeed.com/viewjob?jk=abc123"), {})
        self.assertEqual(posting_key_fields(""), {})


if __name__ == "__main__":
    unittest.main()
