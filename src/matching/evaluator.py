"""
Job evaluation — the model classifies, code scores.

Layer 1 (one call per job): read the CV (docs/user_profile.md), the rules
(docs/matching_rules.md) and the posting; return labels (role type, stack fit,
seniority, backend-language ask, hard fail with a quote from the posting).

Layer 2 (only when layer 1 passes): look up the company with web search —
business, product type, customer markets. Cached per company (MongoDB
collection company_profiles in the pipeline; a JSON file in evals).

score_job() turns the labels into 0–10 with MATCH_WEIGHTS (core/config.py), so
preferences are tuned by editing numbers, not prompts. to_analysis() maps the
result onto the fields the Tracker / Unmatched pages store and show.
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, Optional

from openai import OpenAI

from core.config import MATCH_BLOCKED_COMPANIES, MATCH_WEIGHTS
from core.scraper_utils import assess_job_description, scrub_bullet_location, strip_html

JOB_MODEL = os.getenv("MATCH_JOB_MODEL", "gpt-5.4-mini")
COMPANY_MODEL = os.getenv("MATCH_COMPANY_MODEL", "gpt-5.4-mini")
REASONING_EFFORT = os.getenv("MATCH_REASONING", "low")
MAX_DESCRIPTION_CHARS = 12000

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROLE_TYPES = [
    "frontend",
    "fullstack_frontend_leaning",
    "fullstack_balanced",
    "fullstack_backend_leaning",
    "backend",
    "not_web_dev",
]
HARD_FAIL_KINDS = [
    "none",
    "german_required",
    "non_js_backend_required",
    "leadership",
    "not_web_dev",
    "backend_heavy",
    "too_many_years",
    "contract_or_location",
]
PRODUCT_TYPES = [
    "b2b_saas",
    "b2c_ecommerce_marketplace",
    "consumer_app",
    "fintech",
    "other_software_product",
    "agency_consulting_outsourcing",
    "staffing_recruiter",
    "hardware_industrial_automotive",
    "public_sector_research",
    "other",
]
MARKETS = ["global", "europe", "dach", "germany_only", "unclear"]

JOB_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "description_usable": {"type": "boolean"},
        "hard_fail": {"type": "boolean"},
        "hard_fail_kind": {"type": "string", "enum": HARD_FAIL_KINDS},
        "hard_fail_evidence": {"type": "string"},
        "german_required": {"type": "boolean"},
        "german_evidence": {"type": "string"},
        "role_type": {"type": "string", "enum": ROLE_TYPES},
        "stack_fit": {"type": "string", "enum": ["strong", "partial", "weak"]},
        "seniority": {
            "type": "string",
            "enum": ["junior", "mid", "senior", "lead_or_above"],
        },
        "backend_language_ask": {
            "type": "string",
            "enum": [
                "none",
                "js_only",
                "or_list_with_js",
                "nice_to_have",
                "company_uses",
                "implied_non_js",
                "required_non_js",
            ],
        },
        "ai_product_work": {"type": "boolean"},
        "english_team": {"type": "string", "enum": ["true", "false", "unclear"]},
        "stack": {"type": "string"},
        "summary": {"type": "string"},
        "watch_outs": {"type": "string"},
    },
    "required": [
        "description_usable",
        "hard_fail",
        "hard_fail_kind",
        "hard_fail_evidence",
        "german_required",
        "german_evidence",
        "role_type",
        "stack_fit",
        "seniority",
        "backend_language_ask",
        "ai_product_work",
        "english_team",
        "stack",
        "summary",
        "watch_outs",
    ],
}

COMPANY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "business": {"type": "string"},
        "product_type": {"type": "string", "enum": PRODUCT_TYPES},
        "vertical": {"type": "string"},
        "markets": {"type": "string", "enum": MARKETS},
        "markets_detail": {"type": "string"},
        "is_recruiter_for_other_company": {"type": "boolean"},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "business",
        "product_type",
        "vertical",
        "markets",
        "markets_detail",
        "is_recruiter_for_other_company",
        "sources",
    ],
}


def resolve_data_path(path: str) -> str:
    """Prefer cwd, then the repo root (so Flask cwd does not hide docs/)."""
    if os.path.isabs(path):
        return path
    cwd_path = os.path.abspath(path)
    if os.path.exists(cwd_path):
        return cwd_path
    root_path = os.path.join(_PROJECT_ROOT, path)
    return root_path if os.path.exists(root_path) else cwd_path


def _read_text(path: str) -> str:
    try:
        with open(resolve_data_path(path), "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"Warning: {path} not found")
        return ""


def load_user_profile(path: str = "docs/user_profile.md") -> str:
    """The candidate CV (gitignored, personal)."""
    return _read_text(path)


def load_matching_rules(path: str = "docs/matching_rules.md") -> str:
    """Hard fails + classification rules for layer 1."""
    return _read_text(path)


def is_blocked_match_company(company: Optional[str]) -> bool:
    """True if the company is an aggregator we never put on the Tracker."""
    if not company:
        return False
    lower = str(company).lower()
    return any(name.lower() in lower for name in MATCH_BLOCKED_COMPANIES)


def _client() -> OpenAI:
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def _reasoning(model: str) -> Optional[Dict]:
    return {"effort": REASONING_EFFORT} if model.startswith(("gpt-5", "gpt-6", "o")) else None


class OutOfCreditsError(RuntimeError):
    """The OpenAI account has no credits left; every further call will fail."""


def _raise_if_out_of_credits(exc: Exception) -> None:
    text = str(exc)
    if "insufficient_quota" in text or "credit_balance_exhausted" in text:
        raise OutOfCreditsError(
            "OpenAI account has no credits left — add credits at "
            "https://platform.openai.com/settings/organization/billing/"
        ) from exc


def _usage(resp) -> Dict:
    u = getattr(resp, "usage", None)
    return {
        "input_tokens": getattr(u, "input_tokens", 0) or 0,
        "output_tokens": getattr(u, "output_tokens", 0) or 0,
    }


def classify_job(
    job: Dict,
    cv: Optional[str] = None,
    rules: Optional[str] = None,
    model: str = JOB_MODEL,
) -> Optional[Dict]:
    """Layer 1: labels for one job, or None if the call fails."""
    cv = cv if cv is not None else load_user_profile()
    rules = rules if rules is not None else load_matching_rules()
    description = strip_html(job.get("description") or "")[:MAX_DESCRIPTION_CHARS]
    prompt = f"""## Candidate CV
{cv}

## Rules
{rules}

## Job posting
Title: {job.get('title') or 'N/A'}
Company: {job.get('company') or 'N/A'}
Location: {scrub_bullet_location(job.get('location') or '') or 'N/A'}

{description or '(no description)'}
"""
    kwargs = {
        "model": model,
        "instructions": (
            "You screen job postings for one candidate. Judge like an experienced "
            "tech recruiter who knows her CV. Use only facts in the posting; quote it "
            "for any hard fail or German requirement. Return the JSON schema."
        ),
        "input": prompt,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "job_labels",
                "schema": JOB_SCHEMA,
                "strict": True,
            }
        },
    }
    reasoning = _reasoning(model)
    if reasoning:
        kwargs["reasoning"] = reasoning
    try:
        resp = _client().responses.create(**kwargs)
        labels = json.loads(resp.output_text)
        labels["_usage"] = _usage(resp)
        labels["_model"] = model
        return labels
    except Exception as exc:
        _raise_if_out_of_credits(exc)
        print(f"⚠ classify_job failed for {job.get('title')!r}: {exc}")
        return None


def company_key(name: str) -> str:
    """Normalize a company name for the cache (drop legal suffixes / punctuation)."""
    text = (name or "").lower()
    text = re.sub(r"\b(gmbh|ag|se|kg|ug|inc|ltd|llc|co|group|holding|& co)\b\.?", " ", text)
    return " ".join(re.sub(r"[^a-z0-9äöüß]+", " ", text).split())


def research_company(
    company: str,
    job: Optional[Dict] = None,
    cache: Optional[Dict] = None,
    model: str = COMPANY_MODEL,
) -> Optional[Dict]:
    """Layer 2: business + customer markets via web search, cached by company_key."""
    key = company_key(company)
    if cache is not None and key and key in cache:
        return cache[key]

    hint = ""
    if job:
        snippet = strip_html(job.get("description") or "")[:1500]
        hint = f"\nJob title: {job.get('title')}\nPosting excerpt:\n{snippet}\n"
    prompt = f"""Company: {company}{hint}
Find what this company sells and who its customers are.
- business: what the product does and who uses it, one plain sentence.
- product_type / vertical: classify the company's own business.
- markets: where its customers or users are today (not where offices or staff are).
  global / europe / dach / germany_only, or unclear if no source confirms it.
- markets_detail: countries, regions or number of markets if a source gives them.
- If the company is a recruiter or agency and the posting names the real employer,
  set is_recruiter_for_other_company true and describe the real employer instead.
- sources: the URLs you relied on.
Never guess; say unclear."""
    kwargs = {
        "model": model,
        "tools": [{"type": "web_search"}],
        "input": prompt,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "company_profile",
                "schema": COMPANY_SCHEMA,
                "strict": True,
            }
        },
    }
    reasoning = _reasoning(model)
    if reasoning:
        kwargs["reasoning"] = reasoning
    try:
        resp = _client().responses.create(**kwargs)
        profile = json.loads(resp.output_text)
        profile["_usage"] = _usage(resp)
        profile["_model"] = model
    except Exception as exc:
        _raise_if_out_of_credits(exc)
        print(f"⚠ research_company failed for {company!r}: {exc}")
        return None
    if cache is not None and key:
        cache[key] = profile
    return profile


_PREFERRED_CITIES = re.compile(r"\b(leipzig|halle|berlin)\b", re.I)
_REMOTE = re.compile(r"\bremote\b", re.I)


def score_job(job: Dict, labels: Optional[Dict], company: Optional[Dict] = None) -> Dict:
    """Turn layer 1/2 labels into a 0–10 score plus the reasons behind it."""
    w = MATCH_WEIGHTS
    if not labels:
        return {"score": None, "parts": ["classification failed"]}
    if is_blocked_match_company(job.get("company")):
        return {"score": w["hard_fail_score"], "parts": ["hard fail: blocked company"]}
    if not labels.get("description_usable", True):
        return {"score": 0.0, "parts": ["description unusable"]}
    backend_ask = labels.get("backend_language_ask")
    if labels.get("german_required"):
        return {"score": w["hard_fail_score"], "parts": ["hard fail: german_required"]}
    if backend_ask == "required_non_js":
        return {"score": w["hard_fail_score"], "parts": ["hard fail: non_js_backend_required"]}
    kind = labels.get("hard_fail_kind")
    # The backend-language effect is decided by backend_language_ask above, so a
    # model-side non_js_backend_required hard fail with a softer ask is ignored.
    if labels.get("hard_fail") and kind != "non_js_backend_required":
        return {"score": w["hard_fail_score"], "parts": [f"hard fail: {kind}"]}

    parts = []
    score = w["role_type"].get(labels.get("role_type"), 0.0)
    parts.append(f"role {labels.get('role_type')} {score:+.1f}")
    for field in ("stack_fit", "seniority"):
        delta = w[field].get(labels.get(field), 0.0)
        if delta:
            score += delta
            parts.append(f"{field} {labels.get(field)} {delta:+.1f}")
    delta = w["backend_language_ask"].get(backend_ask, 0.0)
    if delta:
        score += delta
        parts.append(f"backend {backend_ask} {delta:+.1f}")
    if labels.get("ai_product_work"):
        score += w["ai_product_work"]
        parts.append(f"AI product {w['ai_product_work']:+.1f}")

    location = job.get("location") or ""
    if _PREFERRED_CITIES.search(location) or _REMOTE.search(location):
        score += w["preferred_location"]
        parts.append(f"location {w['preferred_location']:+.1f}")

    if company:
        delta = w["product_type"].get(company.get("product_type"), 0.0)
        if delta:
            score += delta
            parts.append(f"{company.get('product_type')} {delta:+.1f}")
        if company.get("is_recruiter_for_other_company"):
            score += w["recruiter_posting"]
            parts.append(f"recruiter posting {w['recruiter_posting']:+.1f}")
        delta = w["markets"].get(company.get("markets"), 0.0)
        if delta:
            score += delta
            parts.append(f"markets {company.get('markets')} {delta:+.1f}")

    score = max(0.0, min(10.0, round(score, 1)))
    return {"score": score, "parts": parts}


class MongoCompanyCache:
    """Dict-like company cache in MongoDB (collection company_profiles)."""

    def __init__(self, collection=None):
        if collection is None:
            from core.db_mongo import get_collection

            collection = get_collection("company_profiles")
        self._col = collection

    def __contains__(self, key) -> bool:
        return self._col.count_documents({"_id": key}, limit=1) > 0

    def __getitem__(self, key) -> Dict:
        doc = self._col.find_one({"_id": key})
        if doc is None:
            raise KeyError(key)
        doc.pop("_id", None)
        doc.pop("cached_at", None)
        return doc

    def __setitem__(self, key, value: Dict) -> None:
        from datetime import datetime

        self._col.replace_one(
            {"_id": key},
            {**value, "cached_at": datetime.now()},
            upsert=True,
        )


def evaluate(
    job: Dict,
    cv: Optional[str] = None,
    rules: Optional[str] = None,
    company_cache=None,
) -> Dict:
    """
    Full path for one job: empty-JD check, layer 1, layer 2 if it passes, score.

    Returns {"labels", "company", "score", "parts"}; score is None when the
    layer-1 call failed.
    """
    assessment = assess_job_description(
        job.get("description") or "",
        title=job.get("title") or "",
        company=job.get("company") or "",
        source=job.get("source") or "",
    )
    if assessment.get("kind") == "empty":
        return {
            "labels": None,
            "company": None,
            "score": 0.0,
            "parts": ["empty description"],
        }
    labels = classify_job(job, cv=cv, rules=rules)
    company = None
    if (
        labels
        and labels.get("description_usable", True)
        and not is_blocked_match_company(job.get("company"))
        and score_job(job, labels)["score"] > MATCH_WEIGHTS["hard_fail_score"]
    ):
        company = research_company(job.get("company") or "", job=job, cache=company_cache)
    result = score_job(job, labels, company)
    return {"labels": labels, "company": company, **result}


# match_gate values stored on jobs / matched_jobs (Unmatched page filter + pills)
_GATE_BY_HARD_FAIL = {
    "german_required": "german",
    "non_js_backend_required": "stack",
    "blocked company": "blocked_company",
}


def _gate(parts) -> str:
    for part in parts:
        if part == "empty description":
            return "empty_desc"
        if part == "description unusable":
            return "incomplete_desc"
        if part.startswith("hard fail: "):
            kind = part[len("hard fail: "):]
            return _GATE_BY_HARD_FAIL.get(kind, kind)
    return "scored"


def _clean(meta: Optional[Dict]) -> Dict:
    return {k: v for k, v in (meta or {}).items() if not k.startswith("_")}


def to_analysis(result: Dict, threshold: float = 7.0) -> Optional[Dict]:
    """Map an evaluate() result onto the stored analysis fields (None if it failed)."""
    score = result.get("score")
    if score is None:
        return None
    labels = result.get("labels") or {}
    company = result.get("company") or {}
    parts = result.get("parts") or []
    gate = _gate(parts)

    reasons = []
    if company.get("business"):
        reasons.append(f"Business: {company['business']}")
    if company.get("markets"):
        detail = company.get("markets_detail") or ""
        reasons.append(
            f"Customers/markets: {company['markets']}" + (f" — {detail}" if detail else "")
        )
    if labels.get("stack"):
        reasons.append(f"Stack: {labels['stack']}")
    reasons.append("Score: " + ", ".join(parts))

    watch = (labels.get("watch_outs") or "").strip()
    red_flags = [watch] if watch and watch.lower() != "none" else []
    disqualification = ""
    if gate == "german":
        disqualification = f"Mandatory German: {labels.get('german_evidence') or ''}".strip()
    elif gate not in ("scored",):
        evidence = labels.get("hard_fail_evidence") or ""
        disqualification = f"{gate}: {evidence}".strip(": ") if evidence else gate

    if score >= threshold:
        recommendation = "Yes"
    elif score >= threshold - 1:
        recommendation = "Maybe"
    else:
        recommendation = "No"

    return {
        "match_score": score,
        "recommendation": recommendation,
        "special_match": False,
        "special_match_reasons": [],
        "disqualification_reason": disqualification,
        "match_reasons": reasons,
        "missing_requirements": [],
        "red_flags": red_flags,
        "nice_to_have_matches": [],
        "summary": labels.get("summary") or (
            "No usable job description scraped; not sent to AI."
            if gate == "empty_desc" else ""
        ),
        "what_youll_do": {"matched": [], "unmatched": []},
        "what_theyre_looking_for": {"matched": [], "unmatched": []},
        "match_gate": gate,
        "needs_refetch": gate in ("empty_desc", "incomplete_desc"),
        "job_labels": _clean(labels),
        "company_profile": _clean(company),
    }
