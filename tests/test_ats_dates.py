"""ATS publish dates: every provider's format parses to a naive datetime."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sources.ats import _dt  # noqa: E402


class AtsDateTest(unittest.TestCase):
    def test_recruitee_utc_suffix(self):
        self.assertEqual(_dt("2026-10-28 13:28:31 UTC"), datetime(2026, 10, 28, 13, 28, 31))

    def test_iso_with_z_and_offset(self):
        self.assertEqual(_dt("2026-10-28T13:28:31Z"), datetime(2026, 10, 28, 13, 28, 31))
        self.assertEqual(_dt("2026-10-28T13:28:31.000+02:00"), datetime(2026, 10, 28, 13, 28, 31))

    def test_date_only_and_empty(self):
        self.assertEqual(_dt("2026-10-28"), datetime(2026, 10, 28))
        self.assertIsNone(_dt(""))
        self.assertIsNone(_dt("not a date"))


if __name__ == "__main__":
    unittest.main()
