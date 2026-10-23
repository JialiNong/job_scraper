"""Check gates: dry-run hard gates on a stored JD (no Chrome, no DB writes)."""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import core.db_mongo as db_mongo  # noqa: E402
from matching.gate_check import check_hard_gates  # noqa: E402

with patch.object(db_mongo, "ensure_indexes"):
    import web.web_app as web_app  # noqa: E402

HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

BODY = (
    "We are building a procurement platform for European mid-market companies. "
    "You will own features end to end across a TypeScript and Node.js backend "
    "and a React frontend, ship to production daily, and work closely with "
    "product and design. Requirements: 5+ years building web products, strong "
    "TypeScript, Node.js and PostgreSQL experience, comfort with cloud "
    "infrastructure and CI/CD, and a pragmatic product mindset. "
)


def _job(lang_line: str, title: str = "Senior Full-Stack Engineer (m/f/d)") -> dict:
    return {
        "title": title,
        "company": "Buying Labs GmbH",
        "location": "Hamburg",
        "source": "linkedin",
        "description": (
            f"About the role: {title} at Buying Labs GmbH in Hamburg. "
            + BODY * 3
            + lang_line
        ),
    }


def _fake_stack(extract=None, gate=None, title_reason=None, automation_reason=None):
    mod = types.ModuleType("matching.stack_gate")
    mod.extract_jd_requirements = lambda job, temperature=0.0: extract
    mod.evaluate_requirement_gate = lambda ex: gate
    mod.find_ai_ml_title_requirement = lambda job: title_reason
    mod.find_automation_title_requirement = lambda job: automation_reason
    return patch.dict(sys.modules, {"matching.stack_gate": mod})


NODE_EXTRACT = {"backend_languages": [{"name": "Node.js", "requiredness": "required"}]}


class CheckHardGatesTests(unittest.TestCase):
    def test_german_plus_passes_every_gate(self):
        with _fake_stack(extract=NODE_EXTRACT):
            out = check_hard_gates(_job("Fluent English required; German is a plus."))
        self.assertTrue(out["would_score"])
        self.assertIsNone(out["blocked_by"])
        by_key = {s["key"]: s["status"] for s in out["steps"]}
        self.assertEqual(by_key["german"], "pass")
        self.assertEqual(by_key["stack"], "pass")

    def test_mandatory_german_blocks(self):
        with _fake_stack(extract=NODE_EXTRACT):
            out = check_hard_gates(_job("Fluent German is required."))
        self.assertEqual(out["blocked_by"], "german")
        self.assertFalse(out["would_score"])

    def test_stack_gate_blocks_and_all_gates_still_reported(self):
        gate = {"kind": "stack", "reason": "Hard backend `Java` (required)", "missing": ["Java"]}
        with _fake_stack(extract={"backend_languages": []}, gate=gate):
            out = check_hard_gates(_job("Fluent German is required."))
        # First blocker in pipeline order wins, but stack is still shown.
        self.assertEqual(out["blocked_by"], "german")
        stack = next(s for s in out["steps"] if s["key"] == "stack")
        self.assertEqual(stack["status"], "block")
        self.assertIn("Java", stack["detail"])

    def test_extract_unavailable_is_skip_not_block(self):
        with _fake_stack(extract=None):
            out = check_hard_gates(_job("Fluent English required."))
        stack = next(s for s in out["steps"] if s["key"] == "stack")
        self.assertEqual(stack["status"], "skip")
        self.assertTrue(out["would_score"])

    def test_stack_module_missing_on_render_is_skip(self):
        with patch.dict(sys.modules, {"matching.stack_gate": None}):
            out = check_hard_gates(_job("Fluent English required."))
        statuses = {s["key"]: s["status"] for s in out["steps"]}
        self.assertEqual(statuses["stack"], "skip")
        self.assertEqual(statuses["ai_ml_title"], "skip")
        self.assertEqual(statuses["automation_title"], "skip")

    def test_automation_title_blocks(self):
        reason = 'Title is a business/process-automation role: "Business Automation Developer"'
        with _fake_stack(extract=NODE_EXTRACT, automation_reason=reason):
            out = check_hard_gates(
                _job("Fluent English required.", title="Business Automation Developer")
            )
        self.assertEqual(out["blocked_by"], "automation_title")
        self.assertFalse(out["would_score"])


class CheckGatesEndpointTests(unittest.TestCase):
    def setUp(self):
        web_app.app.config["LOGIN_DISABLED"] = True
        self.client = web_app.app.test_client()

    def tearDown(self):
        web_app.app.config["LOGIN_DISABLED"] = False

    def _col(self, doc):
        col = types.SimpleNamespace(find_one=lambda *a, **k: doc)
        return patch.object(web_app, "get_collection", return_value=col)

    def test_returns_steps_without_writing(self):
        doc = {"_id": "x", "match_gate": "german", **_job("Fluent English required; German is a plus.")}
        with self._col(doc), _fake_stack(extract=NODE_EXTRACT):
            res = self.client.post("/api/jobs/6ad88c5e2924cb8e92a1e8d9/check-gates")
        data = res.get_json()
        self.assertEqual(res.status_code, 200)
        self.assertTrue(data["would_score"])
        self.assertEqual(data["current_gate"], "german")

    def test_no_description_is_400(self):
        with self._col({"_id": "x", "title": "(Link only) job", "description": ""}):
            res = self.client.post("/api/jobs/6ad88c5e2924cb8e92a1e8d9/check-gates")
        self.assertEqual(res.status_code, 400)

    def test_invalid_id(self):
        res = self.client.post("/api/jobs/not-an-id/check-gates")
        self.assertEqual(res.status_code, 400)


class CheckGatesUiTests(unittest.TestCase):
    def test_card_has_check_gates_button_and_panel(self):
        self.assertIn("function checkGatesForJob(", HTML)
        self.assertIn('class="btn-check-gates"', HTML)
        self.assertIn('id="gate-check-${job._id}"', HTML)

    def test_run_ai_toast_mentions_gate_stop(self):
        self.assertIn("AI scoring was not run", HTML)

    def test_scored_card_checkbox_is_delete_only(self):
        self.assertIn("const fetchable = canFetch && aiHint;", HTML)
        self.assertIn('data-fetchable="${fetchable ? \'1\' : \'0\'}"', HTML)
        self.assertIn(".card-select[data-fetchable=\"1\"]", HTML)

    def test_checkboxes_hidden_until_select_mode(self):
        self.assertIn(".job-grid:not(.select-mode) .card-select-wrap { display: none; }", HTML)
        self.assertIn('id="selectModeBtn"', HTML)
        self.assertIn("function toggleSelectMode()", HTML)


if __name__ == "__main__":
    unittest.main()
