"""matching.evaluator: code-side scoring and storage mapping (no API calls)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from matching import evaluator as ev  # noqa: E402

JOB = {"title": "Senior Frontend Engineer", "company": "Acme", "location": "Berlin, Germany"}


def labels(**over):
    base = {
        "description_usable": True,
        "hard_fail": False,
        "hard_fail_kind": "none",
        "hard_fail_evidence": "",
        "german_required": False,
        "german_evidence": "",
        "role_type": "frontend",
        "stack_fit": "strong",
        "seniority": "senior",
        "backend_language_ask": "none",
        "ai_product_work": False,
        "english_team": "true",
        "stack": "React, TypeScript",
        "summary": "Frontend role.",
        "watch_outs": "none",
        "_usage": {"input_tokens": 1, "output_tokens": 1},
    }
    base.update(over)
    return base


COMPANY = {
    "business": "Online shop software",
    "product_type": "b2b_saas",
    "vertical": "e-commerce",
    "markets": "global",
    "markets_detail": "30 countries",
    "is_recruiter_for_other_company": False,
    "sources": [],
}


class ScoreJobTests(unittest.TestCase):
    def test_strong_frontend_saas_global_scores_high(self):
        res = ev.score_job(JOB, labels(), COMPANY)
        # 7 frontend + 1 stack + 0.3 Berlin + 1 SaaS + 0.7 global = 10
        self.assertEqual(res["score"], 10.0)

    def test_german_required_is_hard_fail(self):
        res = ev.score_job(JOB, labels(german_required=True), COMPANY)
        self.assertEqual(res["score"], 1.0)
        self.assertIn("hard fail: german_required", res["parts"])

    def test_required_non_js_backend_is_hard_fail(self):
        res = ev.score_job(JOB, labels(backend_language_ask="required_non_js"))
        self.assertIn("hard fail: non_js_backend_required", res["parts"])

    def test_implied_non_js_backend_is_only_a_gap(self):
        # Model over-calls the hard fail; backend_language_ask decides.
        res = ev.score_job(
            JOB,
            labels(
                backend_language_ask="implied_non_js",
                hard_fail=True,
                hard_fail_kind="non_js_backend_required",
            ),
        )
        self.assertAlmostEqual(res["score"], 7.3)

    def test_hardware_company_is_penalised(self):
        hw = {**COMPANY, "product_type": "hardware_industrial_automotive", "markets": "dach"}
        res = ev.score_job(JOB, labels(), hw)
        self.assertAlmostEqual(res["score"], 5.8)

    def test_blocked_company_case_insensitive(self):
        job = {**JOB, "company": "HYRA Talent"}
        with patch.object(ev, "MATCH_BLOCKED_COMPANIES", ["Hyra"]):
            res = ev.score_job(job, labels(), COMPANY)
        self.assertIn("hard fail: blocked company", res["parts"])

    def test_unusable_description(self):
        res = ev.score_job(JOB, labels(description_usable=False))
        self.assertEqual(res["score"], 0.0)


class EvaluateTests(unittest.TestCase):
    def test_empty_description_skips_the_model(self):
        with patch.object(ev, "classify_job") as classify:
            res = ev.evaluate({**JOB, "description": ""})
        classify.assert_not_called()
        self.assertEqual(ev.to_analysis(res)["match_gate"], "empty_desc")

    def test_company_lookup_only_after_layer1_passes(self):
        long_jd = "We build React apps for shops. " * 40
        with patch.object(ev, "classify_job", return_value=labels(german_required=True)), \
                patch.object(ev, "research_company") as research:
            ev.evaluate({**JOB, "description": long_jd}, cv="cv", rules="rules")
        research.assert_not_called()

        with patch.object(ev, "classify_job", return_value=labels()), \
                patch.object(ev, "research_company", return_value=COMPANY) as research:
            res = ev.evaluate({**JOB, "description": long_jd}, cv="cv", rules="rules")
        research.assert_called_once()
        self.assertEqual(res["company"], COMPANY)


class ToAnalysisTests(unittest.TestCase):
    def test_scored_job_fields(self):
        res = {"labels": labels(), "company": COMPANY, **ev.score_job(JOB, labels(), COMPANY)}
        out = ev.to_analysis(res, threshold=7.0)
        self.assertEqual(out["match_gate"], "scored")
        self.assertEqual(out["recommendation"], "Yes")
        self.assertTrue(any(r.startswith("Business:") for r in out["match_reasons"]))
        self.assertTrue(any("30 countries" in r for r in out["match_reasons"]))
        self.assertNotIn("_usage", out["job_labels"])
        self.assertEqual(out["red_flags"], [])

    def test_gate_names_match_unmatched_filter(self):
        cases = {
            "german_required": "german",
            "non_js_backend_required": "stack",
            "leadership": "leadership",
        }
        for kind, gate in cases.items():
            lb = labels(hard_fail=True, hard_fail_kind=kind, hard_fail_evidence="quote")
            if kind == "german_required":
                lb["german_required"] = True
            if kind == "non_js_backend_required":
                lb["backend_language_ask"] = "required_non_js"
            res = {"labels": lb, "company": None, **ev.score_job(JOB, lb)}
            self.assertEqual(ev.to_analysis(res)["match_gate"], gate, kind)

    def test_failed_call_returns_none(self):
        self.assertIsNone(ev.to_analysis({"score": None, "parts": []}))


if __name__ == "__main__":
    unittest.main()
