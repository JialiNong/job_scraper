"""AI matcher: skip incomplete JDs, ground skills in the description, low temperature."""
from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from matching import ai_matcher as am  # noqa: E402
from matching.ai_matcher import (  # noqa: E402
    MATCH_AI_TEMPERATURE,
    _ground_analysis_in_description,
    _reclassify_unknown_stack_matches,
    build_matching_prompt,
    description_skip_analysis,
    evaluate_job,
)


FULL_JD = (
    "Snke is hiring a Senior Software Engineer - Full Stack in Munich. "
    "You will build TypeScript and React applications on AWS with CDK. "
    "Excellent English skills, German is a plus. "
    "Requirements: several years of web engineering, TypeScript, React."
)


class PromptAndTemperatureTests(unittest.TestCase):
    def test_prompt_forbids_invention(self):
        prompt = build_matching_prompt(
            {
                "title": "Senior Software Engineer - Full Stack",
                "company": "Snke",
                "location": "Munich",
                "source": "linkedin",
                "description": FULL_JD,
            },
            "profile",
            "criteria",
        )
        self.assertIn("ONLY facts", prompt)
        self.assertIn("not stated in the description", prompt)
        self.assertIn("Never invent", prompt)
        self.assertIn("nice-to-have is **not** mandatory", prompt)
        self.assertIn("is a plus", prompt)
        self.assertIn("if you want more", prompt)
        self.assertIn("Rust", prompt)

    def test_default_temperature_is_zero(self):
        self.assertEqual(MATCH_AI_TEMPERATURE, 0.0)
        params = inspect.signature(evaluate_job).parameters
        self.assertEqual(params["temperature"].default, MATCH_AI_TEMPERATURE)


class IncompleteDescriptionSkipTests(unittest.TestCase):
    def test_skip_payload_marks_refetch(self):
        analysis = description_skip_analysis(
            {
                "kind": "incomplete",
                "reason": "Description too short (80 chars) to match on",
            }
        )
        self.assertEqual(analysis["match_gate"], "incomplete_desc")
        self.assertTrue(analysis["needs_refetch"])
        self.assertTrue(analysis["low_confidence"])
        self.assertEqual(analysis["match_score"], 0)

    @patch("matching.ai_matcher.analyze_job_with_ai")
    @patch("matching.ai_matcher.extract_jd_requirements")
    def test_evaluate_job_skips_ai_when_incomplete(self, extract, analyze):
        job = {
            "title": "Senior Software Engineer - Full Stack",
            "company": "Snke",
            "source": "linkedin",
            "description": (
                "About the job. We are looking for a frontend engineer in Munich. "
                "See more."
            ),
        }
        analysis = evaluate_job(job, "profile", "criteria")
        self.assertEqual(analysis["match_gate"], "incomplete_desc")
        extract.assert_not_called()
        analyze.assert_not_called()

    @patch("matching.ai_matcher.analyze_job_with_ai")
    @patch("matching.ai_matcher.extract_jd_requirements")
    def test_evaluate_job_skips_ai_when_empty(self, extract, analyze):
        job = {"title": "Engineer", "company": "Acme", "description": ""}
        analysis = evaluate_job(job, "profile", "criteria")
        self.assertEqual(analysis["match_gate"], "empty_desc")
        extract.assert_not_called()
        analyze.assert_not_called()


class GroundingTests(unittest.TestCase):
    def test_drops_skills_not_in_description(self):
        analysis = {
            "what_theyre_looking_for": {
                "matched": ["React", "Kubernetes"],
                "unmatched": ["Python backend"],
            },
            "what_youll_do": {"matched": ["Build React UIs"], "unmatched": []},
            "missing_requirements": ["Docker"],
            "nice_to_have_matches": ["TypeScript"],
            "match_reasons": ["React matches the stack"],
            "red_flags": [],
        }
        result = _ground_analysis_in_description(
            {"description": FULL_JD},
            analysis,
        )
        self.assertIn("React", result["what_theyre_looking_for"]["matched"])
        self.assertNotIn("Kubernetes", result["what_theyre_looking_for"]["matched"])
        self.assertNotIn("Python backend", result["what_theyre_looking_for"]["unmatched"])
        self.assertEqual(result["missing_requirements"], [])
        self.assertIn("TypeScript", result["nice_to_have_matches"])
        self.assertTrue(result["grounded_in_description"])
        self.assertTrue(
            any("not stated in the description" in str(f) for f in result["red_flags"])
        )


class UnknownStackMatchTests(unittest.TestCase):
    def test_optional_rust_work_is_unmatched(self):
        analysis = {
            "what_youll_do": {
                "matched": [
                    "Build high-impact TypeScript features",
                    "Work with Rust to extend our Simulation",
                ],
                "unmatched": [],
            },
            "what_theyre_looking_for": {
                "matched": ["React and TypeScript", "Nice-to-have Rust"],
                "unmatched": [],
            },
            "nice_to_have_matches": ["Rust", "TypeScript"],
        }
        result = _reclassify_unknown_stack_matches(analysis)
        self.assertEqual(
            result["what_youll_do"]["matched"],
            ["Build high-impact TypeScript features"],
        )
        self.assertIn(
            "Work with Rust to extend our Simulation",
            result["what_youll_do"]["unmatched"],
        )
        self.assertEqual(
            result["what_theyre_looking_for"]["matched"],
            ["React and TypeScript"],
        )
        self.assertIn("Nice-to-have Rust", result["what_theyre_looking_for"]["unmatched"])
        self.assertEqual(result["nice_to_have_matches"], ["TypeScript"])

    def test_does_not_treat_english_go_as_golang(self):
        analysis = {
            "what_youll_do": {
                "matched": ["See your code go to production within days"],
                "unmatched": [],
            }
        }
        result = _reclassify_unknown_stack_matches(analysis)
        self.assertEqual(
            result["what_youll_do"]["matched"],
            ["See your code go to production within days"],
        )


if __name__ == "__main__":
    unittest.main()
