"""Log Job Clear must restore every form field and status message."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "manual_apply.html").read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    marker = f"function {name}("
    start = HTML.index(marker)
    brace = HTML.index("{", start)
    depth = 0
    for i, ch in enumerate(HTML[brace:], brace):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return HTML[brace : i + 1]
    raise AssertionError(f"could not extract {name}()")


class LogJobClearFormTests(unittest.TestCase):
    def test_clear_button_calls_clearForm(self):
        self.assertRegex(
            HTML,
            r'<button[^>]*id="clearBtn"[^>]*onclick="clearForm\(\)"',
        )
        self.assertNotIn(
            "document.getElementById('urlBox').value=''",
            HTML.split("function clearForm")[0],
        )

    def test_clearForm_resets_all_fields_and_messages(self):
        body = _function_body("clearForm")
        assignments = {
            "urlBox": "''",
            "jobTitle": "''",
            "jobCompany": "''",
            "jobLocation": "''",
            "recommendedBy": "''",
            "jobStatus": "'pending'",
        }
        for field_id, default in assignments.items():
            self.assertRegex(
                body,
                rf"getElementById\('{field_id}'\)\.value = {re.escape(default)}",
                msg=f"{field_id} should reset to {default}",
            )

        self.assertIn("appliedDate", body)
        self.assertIn("todayIsoDate()", body)
        self.assertIn("onStatusChange()", body)

        self.assertIn("statusBanner", body)
        self.assertIn("className = 'status-banner'", body)
        self.assertRegex(body, r"innerHTML = ''")

        self.assertIn("logWrap", body)
        self.assertIn("classList.remove('visible')", body)
        self.assertIn("logBody", body)
        self.assertIn("logCounter", body)

        self.assertIn("clearTimeout(_lookupTimer)", body)
        self.assertIn("_lastLookupKey = ''", body)
        self.assertIn("clearInterval(_pollTimer)", body)

    def test_form_defaults_in_markup(self):
        self.assertRegex(
            HTML,
            r'<select id="jobStatus"[^>]*>[\s\S]*?<option value="pending" selected>',
        )
        self.assertRegex(
            HTML,
            r'<select id="recommendedBy"[^>]*>[\s\S]*?<option value="" selected>None</option>',
        )
        self.assertIn('id="jobTitle"', HTML)
        self.assertIn('id="jobCompany"', HTML)
        self.assertIn('id="jobLocation"', HTML)
        self.assertIn('id="appliedDate"', HTML)
        self.assertIn('id="statusBanner"', HTML)


if __name__ == "__main__":
    unittest.main()
