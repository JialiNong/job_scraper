"""
Dry-run the pre-AI hard gates on a stored Tracker JD.

Same order as ai_matcher.evaluate_job: description quality → German →
AI/ML title → automation title → stack extract + requirement gate. Every
gate runs (so the user sees each verdict), but `blocked_by` is the first
one that would stop the real pipeline. No DB writes, no Chrome, no
scoring call.

stack_gate imports openai, which the Render web image does not ship, so
it is imported lazily and reported as skipped when unavailable.
"""
from __future__ import annotations

from typing import Dict, List

from core.scraper_utils import assess_job_description
from matching.german_gate import find_mandatory_german_requirement

PASS = "pass"
BLOCK = "block"
SKIP = "skip"
ERROR = "error"


def _step(key: str, label: str, status: str, detail: str = "") -> Dict:
    return {"key": key, "label": label, "status": status, "detail": detail}


def _stack_steps(job: Dict) -> List[Dict]:
    try:
        from matching.stack_gate import (
            evaluate_requirement_gate,
            extract_jd_requirements,
            find_ai_ml_title_requirement,
            find_automation_title_requirement,
        )
    except ImportError as e:
        why = f"Not available on this server ({e})"
        return [
            _step("ai_ml_title", "AI/ML title gate", SKIP, why),
            _step("automation_title", "Automation title gate", SKIP, why),
            _step("stack", "Tech stack gate", SKIP, why),
        ]

    steps: List[Dict] = []
    try:
        title_reason = find_ai_ml_title_requirement(job)
    except Exception as e:
        steps.append(_step("ai_ml_title", "AI/ML title gate", ERROR, str(e)))
    else:
        steps.append(
            _step("ai_ml_title", "AI/ML title gate", BLOCK, title_reason)
            if title_reason
            else _step("ai_ml_title", "AI/ML title gate", PASS, "Title is not an AI/ML-core role")
        )

    try:
        automation_reason = find_automation_title_requirement(job)
    except Exception as e:
        steps.append(_step("automation_title", "Automation title gate", ERROR, str(e)))
    else:
        steps.append(
            _step("automation_title", "Automation title gate", BLOCK, automation_reason)
            if automation_reason
            else _step(
                "automation_title",
                "Automation title gate",
                PASS,
                "Title is not a business/process-automation role",
            )
        )

    try:
        extract = extract_jd_requirements(job, temperature=0.0)
    except Exception as e:
        steps.append(_step("stack", "Tech stack gate", ERROR, str(e)))
        return steps
    if not extract:
        steps.append(
            _step(
                "stack",
                "Tech stack gate",
                SKIP,
                "Stack extract unavailable (API key missing or call failed) — real run fails open",
            )
        )
        return steps

    gate = evaluate_requirement_gate(extract)
    if gate:
        steps.append(_step("stack", "Tech stack gate", BLOCK, gate.get("reason") or "Blocked"))
    else:
        langs = [
            f"{item.get('name')} ({item.get('requiredness')})"
            for item in extract.get("backend_languages") or []
            if item.get("name")
        ]
        detail = "Backend asks: " + ", ".join(langs) if langs else "No hard backend requirement found"
        steps.append(_step("stack", "Tech stack gate", PASS, detail))
    return steps


def check_hard_gates(job: Dict) -> Dict:
    """
    Run every hard gate on job (title / company / location / description).

    Returns {"steps": [...], "blocked_by": key or None, "would_score": bool}.
    """
    steps: List[Dict] = []

    assessment = assess_job_description(
        job.get("description", ""),
        title=job.get("title") or "",
        company=job.get("company") or "",
        source=job.get("source") or "",
    )
    if assessment.get("ok"):
        steps.append(_step("description", "JD completeness", PASS, "Description looks complete"))
    else:
        steps.append(
            _step(
                "description",
                "JD completeness",
                BLOCK,
                assessment.get("reason") or "Empty or incomplete description",
            )
        )

    german = find_mandatory_german_requirement(job)
    steps.append(
        _step("german", "German language gate", BLOCK, german)
        if german
        else _step("german", "German language gate", PASS, "No mandatory German requirement")
    )

    steps.extend(_stack_steps(job))

    blocked_by = next((s["key"] for s in steps if s["status"] == BLOCK), None)
    return {
        "steps": steps,
        "blocked_by": blocked_by,
        "would_score": blocked_by is None,
    }
