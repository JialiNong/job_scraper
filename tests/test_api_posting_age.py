"""API sources skip ads published more than API_MAX_POSTING_AGE_DAYS ago."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sources.base import SourceJob, is_too_old  # noqa: E402

NOW = datetime(2026, 11, 10, 12, 0)


def job(posted_at):
    return SourceJob(source="ats", job_id="x", title="Dev", company="Acme",
                     location="Berlin", link="https://a.b/c", posted_at=posted_at)


class PostingAgeTest(unittest.TestCase):
    def test_old_ad_is_skipped(self):
        self.assertTrue(is_too_old(job(NOW - timedelta(days=91)), now=NOW, max_days=90))
        self.assertTrue(is_too_old(job(NOW - timedelta(days=330)), now=NOW, max_days=90))

    def test_recent_ad_is_kept(self):
        self.assertFalse(is_too_old(job(NOW - timedelta(days=89)), now=NOW, max_days=90))

    def test_missing_date_is_kept(self):
        self.assertFalse(is_too_old(job(None), now=NOW, max_days=90))

    def test_zero_disables_the_gate(self):
        self.assertFalse(is_too_old(job(NOW - timedelta(days=900)), now=NOW, max_days=0))


if __name__ == "__main__":
    unittest.main()
