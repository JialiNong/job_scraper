"""
Pre-score gate: extract backend languages + AI/ML-core from the JD, then decide
locally whether matching should stop.

German is handled by german_gate.py before this module runs.
Extract is a cheap JSON call with no resume and no score. Local rules fail
unknown/non-production hard backends and AI/ML-engineering roles.
Extract API failure is fail-open: the full scorer still runs.
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional

from dotenv import load_dotenv
from openai import OpenAI

from core.scraper_utils import strip_html

load_dotenv()

AI_MODEL = os.getenv("AI_MODEL", "gpt-4.1-mini")
AI_API_KEY = os.getenv("OPENAI_API_KEY")

HARD_REQUIREDNESS = frozenset({"required", "implied"})
SOFT_REQUIREDNESS = frozenset({
    "or_list",
    "nice_to_have",
    "company_uses",
    "willingness",
})
VALID_REQUIREDNESS = HARD_REQUIREDNESS | SOFT_REQUIREDNESS

# Candidate production backends only. Python is intentionally absent.
PRODUCTION_BACKENDS = frozenset({"node", "typescript", "javascript"})

# Map JD names onto the buckets above (or onto a gap language).
_BACKEND_ALIASES = {
    "node": "node",
    "node.js": "node",
    "nodejs": "node",
    "node js": "node",
    "express": "node",
    "express.js": "node",
    "expressjs": "node",
    "nest": "node",
    "nestjs": "node",
    "nest.js": "node",
    "fastify": "node",
    "koa": "node",
    "hapi": "node",
    "typescript": "typescript",
    "ts": "typescript",
    "javascript": "javascript",
    "js": "javascript",
    "python": "python",
    "django": "python",
    "flask": "python",
    "fastapi": "python",
    "java": "java",
    "spring": "java",
    "spring boot": "java",
    "springboot": "java",
    "jvm": "java",
    "go": "go",
    "golang": "go",
    "php": "php",
    "laravel": "php",
    "symfony": "php",
    "ruby": "ruby",
    "rails": "ruby",
    "ruby on rails": "ruby",
    "c#": "csharp",
    "csharp": "csharp",
    "dotnet": "csharp",
    ".net": "csharp",
    "asp.net": "csharp",
    "scala": "scala",
    "rust": "rust",
    "kotlin": "kotlin",
    "elixir": "elixir",
    "erlang": "erlang",
    "c++": "cpp",
    "cpp": "cpp",
}

# Obvious AI/ML engineering titles that slipped the scrape blacklist.
_AI_ML_TITLE = re.compile(
    r"\b(?:"
    r"ai\s+engineer|llm(?:\s+\w+){0,3}\s+engineer|"
    r"machine\s+learning(?:\s+engineer)?|ml\s+engineer|"
    r"mlops|applied\s+ai|generative\s+ai|"
    r"prompt\s+engineer|nlp\s+engineer|"
    r"deep\s+learning|research\s+scientist|"
    r"ml\s+platform|ai\s+research"
    r")\b",
    re.I,
)

# Tokens used to detect explicit OR language lists in the raw JD text.
_OR_LANG_TOKEN = (
    r"(?:node\.?js|nodejs|node|typescript|javascript|python|java|golang|go|"
    r"kotlin|php|ruby|scala|rust|c#|csharp|\.net|django|flask|fastapi|"
    r"spring(?:\s*boot)?|express(?:\.js)?|nestjs?|nest\.js)"
)
# "TypeScript, Python or similar" / "Node.js or Python" / "Python and/or TypeScript"
_OR_LANG_LIST_RE = re.compile(
    rf"\b({_OR_LANG_TOKEN})(?:\s*,\s*({_OR_LANG_TOKEN}))*"
    rf"\s+(?:and\s+)?or\s+"
    rf"(?:similar|equivalent|comparable|related(?:\s+modern)?(?:\s+languages?)?"
    rf"|{_OR_LANG_TOKEN})\b",
    re.I,
)
_OR_AND_OR_RE = re.compile(
    rf"\b({_OR_LANG_TOKEN})\s+and/or\s+({_OR_LANG_TOKEN})\b",
    re.I,
)
# Independent hard ask outside an OR phrase (do not demote these to or_list).
_INDEPENDENT_HARD_PATTERNS = (
    r"strong\s+{lang}\b",
    r"{lang}\s+is\s+the\s+primary\b",
    r"primary\s+language[^.]*\b{lang}\b",
    r"production\s+experience\s+with\s+{lang}\b",
    r"familiarity\s+with\s+{lang}[^.]*\bis\s+required\b",
    r"\b{lang}\b[^.]*\bis\s+required\b",
    r"required[^.]*\b{lang}\b",
    r"must\s+(?:have|know|use)\s+[^.]*\b{lang}\b",
    r"experience\s+with\s+{lang}\s+for\s+backend\b",
)


def _canonical_backend(name: str) -> Optional[str]:
    raw = re.sub(r"\s+", " ", str(name or "").strip().lower())
    raw = raw.replace("node js", "node.js")
    if not raw:
        return None
    if raw in _BACKEND_ALIASES:
        return _BACKEND_ALIASES[raw]
    # "Python 3" / "Go (Golang)"
    for alias, canonical in _BACKEND_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", raw):
            return canonical
    return raw.replace(" ", "_")[:40]


def _or_match_languages(match: re.Match) -> List[str]:
    """Parse every language token from an OR-phrase match (not just capture groups)."""
    chunk = match.group(0)
    chunk = re.sub(
        r"\s+(?:and\s+)?or\s+(?:similar|equivalent|comparable|"
        r"related(?:\s+modern)?(?:\s+languages?)?)\s*$",
        "",
        chunk,
        flags=re.I,
    )
    chunk = re.sub(r"\s+and/or\s+", ",", chunk, flags=re.I)
    chunk = re.sub(r"\s+or\s+", ",", chunk, flags=re.I)
    names: List[str] = []
    for part in re.split(r"\s*,\s*", chunk):
        part = part.strip()
        if not part:
            continue
        canonical = _canonical_backend(part)
        if canonical:
            names.append(canonical)
    return list(dict.fromkeys(names))


def find_or_language_groups(text: str) -> List[List[str]]:
    """Deterministic OR groups from JD wording (comma+or, or, and/or)."""
    if not text:
        return []
    groups: List[List[str]] = []
    seen = set()
    for pattern in (_OR_LANG_LIST_RE, _OR_AND_OR_RE):
        for match in pattern.finditer(text):
            names = _or_match_languages(match)
            if len(names) < 2:
                continue
            key = tuple(names)
            if key in seen:
                continue
            seen.add(key)
            groups.append(names)
    return groups


def _independently_hard_required(lang: str, text: str, or_spans: List[tuple]) -> bool:
    """True when the JD hard-requires `lang` outside any detected OR phrase."""
    if not text or not lang:
        return False

    surfaces = {re.escape(lang)}
    for alias, canonical in _BACKEND_ALIASES.items():
        if canonical == lang:
            surfaces.add(re.escape(alias))
    lang_alt = "|".join(sorted(surfaces, key=len, reverse=True))

    for pattern_tmpl in _INDEPENDENT_HARD_PATTERNS:
        pattern = re.compile(pattern_tmpl.format(lang=rf"(?:{lang_alt})"), re.I)
        for match in pattern.finditer(text):
            start, _end = match.span()
            if any(span_start <= start < span_end for span_start, span_end in or_spans):
                continue
            return True
    return False


def _or_phrase_spans(text: str) -> List[tuple]:
    spans: List[tuple] = []
    for pattern in (_OR_LANG_LIST_RE, _OR_AND_OR_RE):
        for match in pattern.finditer(text or ""):
            spans.append(match.span())
    return spans


def _evidence_for_group(description: str, group: List[str]) -> str:
    for pattern in (_OR_LANG_LIST_RE, _OR_AND_OR_RE):
        for match in pattern.finditer(description or ""):
            names = set(_or_match_languages(match))
            if names.issuperset(group) or names == set(group):
                return match.group(0).strip()[:240]
    return ""


def apply_or_list_heuristics(description: str, extract: Dict) -> Dict:
    """
    Force explicit JD OR language lists to `or_list`, even if the model labeled
    them implied/required (e.g. duties said "work across TypeScript, Python").

    Does not demote a language that is independently hard-required outside the
    OR phrase (e.g. "Strong Python skills, it's the primary language").
    """
    groups = find_or_language_groups(description)
    if not groups:
        return extract

    spans = _or_phrase_spans(description)
    languages = list(extract.get("backend_languages") or [])
    by_name = {item.get("name"): item for item in languages if item.get("name")}

    for group in groups:
        demote = [
            name for name in group
            if not _independently_hard_required(name, description, spans)
        ]
        if len(demote) < 2:
            # Need at least two soft OR options; otherwise leave model labels.
            continue
        evidence = _evidence_for_group(description, demote)
        for name in demote:
            existing = by_name.get(name)
            if existing:
                if existing.get("requiredness") in HARD_REQUIREDNESS:
                    existing["requiredness"] = "or_list"
                    if evidence and not existing.get("evidence"):
                        existing["evidence"] = evidence
            else:
                item = {
                    "name": name,
                    "requiredness": "or_list",
                    "evidence": evidence,
                }
                languages.append(item)
                by_name[name] = item

    or_groups: List[List[str]] = list(extract.get("backend_or_groups") or [])
    existing_keys = {tuple(g) for g in or_groups}
    for group in groups:
        soft = [
            name for name in group
            if by_name.get(name, {}).get("requiredness") == "or_list"
        ]
        soft = list(dict.fromkeys(soft))
        if len(soft) < 2:
            continue
        key = tuple(soft)
        if key not in existing_keys:
            or_groups.append(soft)
            existing_keys.add(key)

    extract = dict(extract)
    extract["backend_languages"] = languages
    extract["backend_or_groups"] = or_groups
    return extract


def find_ai_ml_title_requirement(job: Dict) -> Optional[str]:
    """Reject obvious AI/ML engineering titles without an extract call."""
    title = str(job.get("title") or "").strip()
    if not title or not _AI_ML_TITLE.search(title):
        return None
    return f"Title is an AI/ML engineering role: \"{title}\""


def extract_jd_requirements(
    job: Dict,
    temperature: float = 0.0,
) -> Optional[Dict]:
    """
    Cheap structured extract: backend languages + whether the role is AI/ML-core.

    Returns None if the API key is missing or the call fails (fail-open).
    """
    if not AI_API_KEY:
        print("Warning: AI API key missing — skip stack extract (fail-open)")
        return None

    description = strip_html(str(job.get("description") or ""))[:6000]
    title = job.get("title") or "N/A"
    company = job.get("company") or "N/A"

    prompt = f"""Extract backend-language requirements and whether this is an AI/ML-engineering role.

Do NOT score the candidate. Do NOT look at a resume. JSON only.

## Job
**Title**: {title}
**Company**: {company}

**Job Description**:
{description}

## Rules

### Backend languages
List only **backend** languages / backend frameworks (Python, Java, Go, Node, Kotlin, PHP, Ruby, C#, Scala, Rust, Django, Spring, Express, Nest, …).
Do NOT list frontend frameworks (React, Vue, Angular, Next.js as UI, CSS).

For each item set `requiredness` to exactly one of:
- required — explicit must / required / mandatory, or the primary backend of the role
- implied — listed under Requirements / What you'll bring / Our stack **for this job**, even without the word must-have
- or_list — one option in an OR ("Node.js or Python", "Python and/or TypeScript backend")
- nice_to_have — plus / preferred / advantage / bonus
- company_uses — another team or the existing system uses it; this role does not have to write it (e.g. frontend owns React, "our backend is Go"; or a company tech dump like "our platform is built with Go, Python, Scala")
- willingness — junior/learn-on-the-job / "basic knowledge, exposure, or willingness to develop"

AND is the default when Requirements list languages without or / and/or / either.
Two separate bullets both asking for production Python and production Node are AND, not OR.
Only use `or_list` and `backend_or_groups` when the JD clearly says or / and/or / either.
Do NOT put AND languages into `backend_or_groups`.

Explicit OR examples (all members `or_list`, and one `backend_or_groups` entry):
- "Experience with TypeScript, Python or similar modern languages"
- "Backend in Node.js or Python" / "Python and/or TypeScript"

Duties / tech dumps are NOT hard AND by themselves:
- "Work across TypeScript, Python, React and modern full-stack technologies" alone does not make Python `required`/`implied` when Requirements use OR (or state no backend language).

TypeScript/JavaScript: include them when they are a backend ask (Node/Express/Nest) **or** when they appear in an OR language list with other backends ("TypeScript, Python or similar"). Omit only frontend-only TS/JS with no backend/OR language meaning.

### AI/ML-core
`ai_ml_core` is true only when the **job itself** is AI/ML engineering: AI Engineer, ML Engineer, LLM Engineer, training/fine-tuning models, ML platforms, research, PyTorch/TensorFlow as core work.

`ai_ml_core` is false when:
- the role is frontend/fullstack/product engineering at an AI company
- AI-assisted coding (Copilot) or shipping LLM **product** features (prompt → UI → human review)
- the company builds AI products but this hire owns web UI / product

## JSON
{{
  "role_focus": "frontend|fullstack|backend|ai_ml|devops|other",
  "ai_ml_core": false,
  "ai_ml_reason": "<short why, or empty>",
  "backend_languages": [
    {{
      "name": "<python|node|java|go|kotlin|...>",
      "requiredness": "required|implied|or_list|nice_to_have|company_uses|willingness",
      "evidence": "<short quote from the JD>"
    }}
  ],
  "backend_or_groups": [["node", "python"]]
}}
"""

    try:
        client = OpenAI(api_key=AI_API_KEY)
        response = client.chat.completions.create(
            model=AI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract backend languages and AI/ML-core from a job description. "
                        "No resume, no match score. Distinguish hard asks vs OR vs nice-to-have "
                        "vs company_uses vs willingness. Product engineers at AI companies are "
                        "not ai_ml_core. JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        parsed = json.loads(raw)
        normalized = _normalize_extract(parsed)
        return apply_or_list_heuristics(description, normalized)
    except Exception as e:
        print(f"Warning: stack extract failed ({e}) — fail-open to full scorer")
        return None


def _normalize_extract(raw) -> Dict:
    if not isinstance(raw, dict):
        raw = {}

    languages = []
    for item in raw.get("backend_languages") or []:
        if not isinstance(item, dict):
            continue
        canonical = _canonical_backend(item.get("name") or "")
        if not canonical:
            continue
        requiredness = str(item.get("requiredness") or "implied").strip().lower()
        if requiredness not in VALID_REQUIREDNESS:
            requiredness = "implied"
        evidence = str(item.get("evidence") or "").strip()[:240]
        languages.append({
            "name": canonical,
            "requiredness": requiredness,
            "evidence": evidence,
        })

    or_groups: List[List[str]] = []
    for group in raw.get("backend_or_groups") or []:
        if not isinstance(group, (list, tuple)):
            continue
        names = []
        for part in group:
            canonical = _canonical_backend(part)
            if canonical:
                names.append(canonical)
        # Unique, keep order
        deduped = list(dict.fromkeys(names))
        if len(deduped) >= 2:
            or_groups.append(deduped)

    ai_ml_core = raw.get("ai_ml_core")
    if isinstance(ai_ml_core, str):
        ai_ml_core = ai_ml_core.strip().lower() in ("true", "yes", "1")
    else:
        ai_ml_core = bool(ai_ml_core)

    role_focus = str(raw.get("role_focus") or "other").strip().lower()
    if role_focus not in {"frontend", "fullstack", "backend", "ai_ml", "devops", "other"}:
        role_focus = "other"

    return {
        "role_focus": role_focus,
        "ai_ml_core": ai_ml_core,
        "ai_ml_reason": str(raw.get("ai_ml_reason") or "").strip()[:300],
        "backend_languages": languages,
        "backend_or_groups": or_groups,
    }


def _requiredness_by_name(extract: Dict) -> Dict[str, str]:
    """Last hard label wins over or_list when the model tags the same language twice."""
    labels: Dict[str, str] = {}
    for item in extract.get("backend_languages") or []:
        name = item.get("name") or ""
        requiredness = item.get("requiredness") or ""
        if not name or requiredness not in VALID_REQUIREDNESS:
            continue
        prev = labels.get(name)
        if prev in HARD_REQUIREDNESS and requiredness not in HARD_REQUIREDNESS:
            continue
        labels[name] = requiredness
    return labels


def evaluate_requirement_gate(extract: Dict) -> Optional[Dict]:
    """
    Local fail/pass. Returns a gate payload on reject, else None (continue scoring).

    Payload: {kind: "ai_ml"|"stack", reason: str, missing: [str]}

    `backend_or_groups` only counts when every member is labeled `or_list`.
    A language tagged required/implied is always an AND ask — listing it next
    to Node.js does not make it an OR.
    """
    if extract.get("ai_ml_core"):
        detail = extract.get("ai_ml_reason") or "JD is an AI/ML engineering role"
        return {
            "kind": "ai_ml",
            "reason": (
                "AI/ML-core role (not product/frontend engineering that uses AI): "
                f"{detail}"
            ),
            "missing": ["AI/ML engineering experience (training/models/LLM stack)"],
        }

    labels = _requiredness_by_name(extract)
    evidence_by_name = {}
    for item in extract.get("backend_languages") or []:
        name = item.get("name") or ""
        if name and name not in evidence_by_name:
            evidence_by_name[name] = item.get("evidence") or ""

    missing: List[str] = []
    reasons: List[str] = []

    for name, requiredness in labels.items():
        if requiredness not in HARD_REQUIREDNESS:
            continue
        if name in PRODUCTION_BACKENDS:
            continue
        evidence = evidence_by_name.get(name) or ""
        snippet = f" — \"{evidence}\"" if evidence else ""
        missing.append(f"{name} (hard backend; candidate has no production experience)")
        reasons.append(f"Hard backend `{name}` ({requiredness}){snippet}")

    checked_or_groups = set()
    for group in extract.get("backend_or_groups") or []:
        key = tuple(group)
        if key in checked_or_groups:
            continue
        checked_or_groups.add(key)
        if not group:
            continue
        # Invented OR groups must not override AND required/implied labels.
        if any(labels.get(member) in HARD_REQUIREDNESS for member in group):
            continue
        if not all(labels.get(member) == "or_list" for member in group):
            continue
        if any(member in PRODUCTION_BACKENDS for member in group):
            continue
        label = " or ".join(group)
        missing.append(
            f"{label} (OR-list; none is a production backend the candidate has)"
        )
        reasons.append(
            f"OR-list `{label}` has no Node/TypeScript/JavaScript backend option"
        )

    if not missing:
        return None

    return {
        "kind": "stack",
        "reason": (
            "Required/implied backend is not in the candidate's production stack "
            "(Node.js / TypeScript / JavaScript). " + "; ".join(reasons)
        ),
        "missing": missing,
    }


def requirement_disqualification_analysis(gate: Dict, extract: Optional[Dict] = None) -> Dict:
    """Minimal analysis payload — no years / domain / Special Match scoring."""
    kind = gate.get("kind") or "stack"
    reason = gate.get("reason") or "Requirement gate failed"
    missing = gate.get("missing") or []
    looking_unmatched = list(missing)

    if kind == "ai_ml":
        summary = "Stopped at AI/ML-core gate; other requirements were not evaluated."
        red_flag = "AI/ML engineering role — skipped remaining matching"
    else:
        summary = "Stopped at backend-stack gate; other requirements were not evaluated."
        red_flag = "Unknown or non-production required backend — skipped remaining matching"

    return {
        "match_score": 1.0,
        "recommendation": "No",
        "special_match": False,
        "special_match_reasons": [],
        "disqualification_reason": reason,
        "what_youll_do": {"matched": [], "unmatched": []},
        "what_theyre_looking_for": {
            "matched": [],
            "unmatched": looking_unmatched,
        },
        "match_reasons": [],
        "missing_requirements": missing,
        "red_flags": [red_flag],
        "nice_to_have_matches": [],
        "summary": summary,
        "match_gate": kind,
        "requirement_extract": extract or {},
    }


def format_extract_for_prompt(extract: Optional[Dict]) -> str:
    """Inject into the scoring prompt so the model does not re-litigate the gate."""
    if not extract:
        return ""

    lines = ["## Pre-extracted JD requirements (already gated in code — do not re-open)"]
    lines.append(f"- Role focus: {extract.get('role_focus') or 'other'}")
    lines.append(
        f"- AI/ML-core: no"
        + (f" ({extract.get('ai_ml_reason')})" if extract.get("ai_ml_reason") else "")
    )

    hard: List[str] = []
    soft: List[str] = []
    for item in extract.get("backend_languages") or []:
        bit = f"{item.get('name')} [{item.get('requiredness')}]"
        evidence = item.get("evidence") or ""
        if evidence:
            bit += f" — \"{evidence}\""
        if item.get("requiredness") in HARD_REQUIREDNESS:
            hard.append(bit)
        else:
            soft.append(bit)

    or_groups = extract.get("backend_or_groups") or []
    if or_groups:
        lines.append(
            "- Backend OR-lists: "
            + "; ".join(" or ".join(group) for group in or_groups)
        )
    lines.append("- Hard backend (required/implied): " + "; ".join(hard) if hard else "- Hard backend: none")
    if soft:
        lines.append("- Other backend labels: " + "; ".join(soft))

    lines.append(
        "This job already passed the backend-stack and AI/ML-core gates. "
        "Do not fail it again for those reasons. `company_uses` is not a hard backend. "
        "OR-lists that include Node/TypeScript/JavaScript already passed."
    )
    return "\n".join(lines)
