"""Backend-stack gate: only explicit hard asks block; soft wording passes."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from matching.stack_gate import (  # noqa: E402
    apply_soft_wording_heuristics,
    evaluate_requirement_gate,
    find_automation_title_requirement,
)


def _extract(*languages):
    return {
        "role_focus": "fullstack",
        "ai_ml_core": False,
        "backend_languages": [
            {"name": name, "requiredness": req, "evidence": ""} for name, req in languages
        ],
        "backend_or_groups": [],
    }


def _label(extract, name):
    for item in extract["backend_languages"]:
        if item["name"] == name:
            return item["requiredness"]
    return None


class EvaluateGateTests(unittest.TestCase):
    def test_required_gap_language_blocks(self):
        gate = evaluate_requirement_gate(_extract(("java", "required")))
        self.assertEqual(gate["kind"], "stack")

    def test_implied_gap_language_passes_to_scorer(self):
        self.assertIsNone(evaluate_requirement_gate(_extract(("go", "implied"))))

    def test_database_is_not_a_backend_language(self):
        self.assertIsNone(
            evaluate_requirement_gate(
                _extract(("node", "required"), ("postgresql", "required"), ("sql", "required"))
            )
        )

    def test_stray_key_does_not_block(self):
        self.assertIsNone(evaluate_requirement_gate(_extract(("backend_languages", "required"))))


class SoftWordingTests(unittest.TestCase):
    def test_nect_ideally_go_beats_duty_mention(self):
        text = (
            "You are responsible for:\n"
            "Develop reliable backend services: Design and implement Go and "
            "React/TypeScript services that power our products.\n"
            "You bring with you:\n"
            "Experience building APIs and backend services, ideally using Go.\n"
            "Fluent English required.\n"
        )
        out = apply_soft_wording_heuristics(text, _extract(("go", "required")))
        self.assertEqual(_label(out, "go"), "nice_to_have")
        self.assertIsNone(evaluate_requirement_gate(out))

    def test_usercentrics_for_example_list_with_node(self):
        text = (
            "Strong proficiency in modern full-stack development, for example "
            "TypeScript/Node.js, .NET and React."
        )
        out = apply_soft_wording_heuristics(text, _extract(("csharp", "required")))
        self.assertEqual(_label(out, "csharp"), "or_list")
        self.assertIsNone(evaluate_requirement_gate(out))

    def test_searoutes_core_api_is_company_system(self):
        text = "Today our core API is Java and Spring, and the identity layer has Java in it too."
        out = apply_soft_wording_heuristics(text, _extract(("java", "required")))
        self.assertEqual(_label(out, "java"), "company_uses")
        self.assertIsNone(evaluate_requirement_gate(out))

    def test_explicit_required_elsewhere_stays_hard(self):
        text = (
            "Ideally you have used Go before. "
            "Familiarity with Java and Spring Boot is required."
        )
        out = apply_soft_wording_heuristics(text, _extract(("java", "required")))
        self.assertEqual(_label(out, "java"), "required")
        self.assertIsNotNone(evaluate_requirement_gate(out))

    def test_strong_go_not_demoted_by_ideally_elsewhere(self):
        text = "Strong Go skills. Ideally Go experience at scale."
        out = apply_soft_wording_heuristics(text, _extract(("go", "required")))
        self.assertEqual(_label(out, "go"), "required")

    def test_plus_on_another_language_does_not_demote(self):
        text = "Strong object-oriented and functional programming skills: Java / Python (Rust is a plus)."
        out = apply_soft_wording_heuristics(
            text, _extract(("java", "required"), ("python", "required"))
        )
        self.assertEqual(_label(out, "java"), "required")
        self.assertEqual(_label(out, "python"), "required")

    def test_language_is_a_plus(self):
        out = apply_soft_wording_heuristics(
            "Go experience is a plus.", _extract(("go", "implied"))
        )
        self.assertEqual(_label(out, "go"), "nice_to_have")

    def test_example_list_without_node_stays_hard(self):
        text = "Backend experience, for example Java or Kotlin with Spring."
        out = apply_soft_wording_heuristics(text, _extract(("java", "required")))
        self.assertEqual(_label(out, "java"), "required")


class AutomationTitleGateTests(unittest.TestCase):
    def test_business_automation_developer_blocks(self):
        reason = find_automation_title_requirement(
            {"title": "Business Automation Developer (all genders) with verification"}
        )
        self.assertIsNotNone(reason)
        self.assertIn("Business Automation Developer", reason)

    def test_process_and_workflow_automation_titles_block(self):
        for title in (
            "Process Automation Engineer",
            "Workflow Automation Specialist",
            "Senior Automation Developer",
            "RPA Developer",
        ):
            with self.subTest(title=title):
                self.assertIsNotNone(find_automation_title_requirement({"title": title}))

    def test_ordinary_engineering_titles_pass(self):
        for title in (
            "Senior Frontend Engineer (React)",
            "Fullstack Developer (Node.js/React)",
            "Test Automation is not in this title",
        ):
            with self.subTest(title=title):
                self.assertIsNone(find_automation_title_requirement({"title": title}))

    def test_missing_title_passes(self):
        self.assertIsNone(find_automation_title_requirement({}))


if __name__ == "__main__":
    unittest.main()
