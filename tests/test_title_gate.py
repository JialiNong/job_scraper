"""Pre-click title gate: blacklist → auto-pass keywords → AI title check."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core import scraper_utils as su  # noqa: E402


class TitleGateTests(unittest.TestCase):
    def test_software_engineer_is_not_auto_passed(self):
        with patch.object(su, "is_title_relevant_by_ai", return_value=False) as ai:
            skip, _ = su.should_skip_title_before_click(
                "Qt/QML Simulation HMI Software Engineer (d/m/f)"
            )
        ai.assert_called_once()
        self.assertTrue(skip)

    def test_frontend_and_fullstack_auto_pass(self):
        with patch.object(su, "is_title_relevant_by_ai") as ai:
            for title in ("Senior Frontend Engineer", "Full Stack Engineer (m/w/d)",
                          "Fullstack Engineer", "Product Engineer"):
                skip, reason = su.should_skip_title_before_click(title)
                self.assertFalse(skip, title)
                self.assertIn("AUTO_PASS", reason)
        ai.assert_not_called()

    def test_forward_deployed_engineer_is_no_longer_blacklisted(self):
        title = "Forward Deployed Engineer (TypeScript)"
        self.assertFalse(su.is_title_excluded(title))
        with patch.object(su, "is_title_relevant_by_ai") as ai:
            skip, _ = su.should_skip_title_before_click(title)
        ai.assert_not_called()
        self.assertFalse(skip)

    def test_blacklist_still_wins(self):
        skip, reason = su.should_skip_title_before_click("Lead Frontend Engineer")
        self.assertTrue(skip)
        self.assertIn("excluded", reason)


if __name__ == "__main__":
    unittest.main()
