#!/usr/bin/env python3
"""
AI-Powered Job Matching System
Uses AI to analyze job listings and match them with user profile and preferences
"""
import sys
import os
import re
from datetime import datetime
from typing import List, Dict, Optional

# Add src/ to path so package imports work when run as a script
_SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

from openai import OpenAI
from dotenv import load_dotenv

from core.db_mongo import (
    init_db,
    get_collection,
    get_new_jobs,
    is_job_id_exists,
    mark_job_as_matched,
)
from core.config import MATCH_BLOCKED_COMPANIES
from core.scraper_utils import (
    strip_html,
    indeed_job_id_variants,
    is_usable_job_description,
    scrub_bullet_location,
)
from matching.german_gate import (
    find_mandatory_german_requirement,
    german_disqualification_analysis,
)
from matching.stack_gate import (
    extract_jd_requirements,
    evaluate_requirement_gate,
    find_ai_ml_title_requirement,
    format_extract_for_prompt,
    requirement_disqualification_analysis,
)

load_dotenv()

# AI Configuration
AI_MODEL = os.getenv("AI_MODEL", "gpt-4.1-mini")  # or "claude-3-5-sonnet-20241022"
AI_API_KEY = os.getenv("OPENAI_API_KEY")  # or ANTHROPIC_API_KEY
MATCH_THRESHOLD = float(os.getenv("MATCH_THRESHOLD", "7.0"))  # Minimum match score (0-10)


def is_blocked_match_company(company: Optional[str]) -> bool:
    """True if scraped company is an aggregator we never put on the Tracker."""
    if not company:
        return False
    lower = str(company).lower()
    return any(name in lower for name in MATCH_BLOCKED_COMPANIES)


def load_user_profile(profile_path: str = "docs/user_profile.md") -> str:
    """
    Load user's resume and profile information.
    
    Args:
        profile_path: Path to the user profile/resume file (supports .txt, .md)
        
    Returns:
        User profile content as string
    """
    try:
        with open(profile_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"Warning: Profile file not found at {profile_path}")
        return ""


def load_matching_criteria(criteria_path: str = "docs/matching_criteria.md") -> str:
    """
    Load user's custom matching criteria and preferences.
    
    Args:
        criteria_path: Path to the matching criteria file
        
    Returns:
        Matching criteria as string
    """
    try:
        with open(criteria_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"Warning: Criteria file not found at {criteria_path}")
        return ""


def build_matching_prompt(
    job: Dict,
    user_profile: str,
    criteria: str,
    extract: Optional[Dict] = None,
) -> str:
    """
    Build the AI prompt for job matching analysis.
    
    Args:
        job: Job data dictionary
        user_profile: User's resume/profile
        criteria: Custom matching criteria
        extract: Optional pre-extracted backend / AI-ML labels from stack_gate
        
    Returns:
        Formatted prompt string
    """
    # Strip HTML tags first so the character budget covers actual text, not markup
    raw_description = job.get("description", "")
    job_description = strip_html(raw_description)[:6000]
    extract_block = format_extract_for_prompt(extract)
    extract_section = f"\n{extract_block}\n" if extract_block else ""
    
    prompt = f"""You are a professional career advisor. Analyze if this job posting matches the candidate's profile and preferences.

## Job Information
**Title**: {job.get('title', 'N/A')}
**Company**: {job.get('company', 'N/A')}
**Location**: {scrub_bullet_location(job.get('location', '')) or 'N/A'}
**Source**: {job.get('source', 'N/A')}
**Link**: {job.get('link', 'N/A')}

**Job Description**:
{job_description}
{extract_section}
## Candidate Profile
{user_profile}

## Matching Criteria (FOLLOW STRICTLY — single source of truth for scoring)
{criteria}

## Task Process

Code already ran the German gate, backend-stack gate, and AI/ML-core gate. This job passed them (or extract failed open). Do **not** fail again for mandatory German, a hard unknown backend, or an AI/ML-engineering title unless the extract block is missing and Matching Criteria clearly require it.

### 1. Parse the JD
Read the full job description and split it into two lists of atomic items (one responsibility or requirement per bullet). Paraphrase clearly; do not invent items that are not in the JD.

1. **What you'll do** — day-to-day responsibilities, duties, tasks, ownership
2. **What they're looking for** — qualifications, skills, years of experience, languages, education, must-haves and nice-to-haves

Then compare EACH item against the candidate profile:
- **matched**: the candidate can reasonably do / already has this, based on the resume
- **unmatched**: the candidate cannot do this, or it is a gap

These two breakdowns MUST appear in the JSON.

### 2. Score using Matching Criteria
Apply remaining Matching Criteria (years, DevOps, required skills, nice-to-have bonus, domain bonus, Special Match). Do not invent extra disqualification rules beyond that document.

If Special Match A, B, or C applies, set `special_match` true and score **9–10** (Leipzig: 9.5–10). Do **not** cap these at 8.
Category C is frontend-leaning fullstack **only when the JD does not hard-require years, a specific backend language, or German**. If the JD does require those, follow original hard-requirement rules — do not mark special_match.
`company_uses` backend (another team's stack) is not a hard backend language.

## JSON Response Format

Respond ONLY with valid JSON:
{{
    "match_score": <number 0-10>,
    "recommendation": "<Yes/No/Maybe>",
    "special_match": <true if Special Match A/B/C applies, else false>,
    "special_match_reasons": ["<Why this is a Special Match, e.g. Pure frontend + React full match. Add 'Located in Leipzig' ONLY if Location/JD literally says Leipzig>"],
    "disqualification_reason": "<Only include this field if score ≤ 3, provide specific reason>",
    "what_youll_do": {{
        "matched": ["<responsibility the candidate can do>"],
        "unmatched": ["<responsibility the candidate cannot do>"]
    }},
    "what_theyre_looking_for": {{
        "matched": ["<requirement the candidate meets>"],
        "unmatched": ["<requirement the candidate does not meet>"]
    }},
    "match_reasons": [
        "<List specific matching points>"
    ],
    "missing_requirements": [
        "<List missing REQUIRED skills only>"
    ],
    "red_flags": [
        "<List concerning points>"
    ],
    "nice_to_have_matches": [
        "<List nice-to-have skills candidate HAS>"
    ],
    "summary": "<Brief 1-2 sentence summary explaining the match score>"
}}

EXAMPLE — disqualified (score ≤ 3) if extract failed open and Python/Django is a hard backend:
{{
    "match_score": 2.0,
    "recommendation": "No",
    "special_match": false,
    "special_match_reasons": [],
    "disqualification_reason": "Must-have backend is Python/Django; candidate has no production Python",
    "what_youll_do": {{
        "matched": ["Build React user interfaces", "Collaborate with product and design"],
        "unmatched": ["Own Django REST APIs"]
    }},
    "what_theyre_looking_for": {{
        "matched": ["5+ years frontend experience", "React and TypeScript"],
        "unmatched": ["3+ years Python/Django as mandatory backend"]
    }},
    "match_reasons": ["Frontend React experience matches requirement"],
    "missing_requirements": ["Python (hard backend)", "3+ years backend experience"],
    "red_flags": ["Backend-heavy role", "Python backend required"],
    "nice_to_have_matches": [],
    "summary": "Fails hard backend requirement of production Python."
}}

EXAMPLE — OR-list backend (Node or Python) already passed the stack gate; ordinary good match:
{{
    "match_score": 8.0,
    "recommendation": "Yes",
    "special_match": false,
    "special_match_reasons": [],
    "what_youll_do": {{
        "matched": ["Design full-stack apps with React", "Build APIs and UIs", "Own product lifecycle"],
        "unmatched": []
    }},
    "what_theyre_looking_for": {{
        "matched": ["React frontend", "TypeScript", "Node.js or Python backend", "SQL/APIs"],
        "unmatched": []
    }},
    "match_reasons": [
        "React and TypeScript align with candidate strengths",
        "Node.js satisfies the Node-or-Python backend OR-list"
    ],
    "missing_requirements": [],
    "red_flags": [],
    "nice_to_have_matches": ["Docker", "CI/CD basics"],
    "summary": "Strong React/TypeScript fit; Node.js covers the backend OR-list."
}}

EXAMPLE — good ordinary match (score 7-8, not special):
{{
    "match_score": 8.0,
    "recommendation": "Yes",
    "special_match": false,
    "special_match_reasons": [],
    "what_youll_do": {{
        "matched": ["Develop React/TypeScript features", "Improve web performance", "Work with designers on UI"],
        "unmatched": []
    }},
    "what_theyre_looking_for": {{
        "matched": ["5+ years frontend", "React and TypeScript", "E-commerce experience"],
        "unmatched": ["GraphQL in production"]
    }},
    "match_reasons": [
        "Frontend skills align perfectly with React requirement",
        "Experience level matches (5-7 years required)",
        "E-commerce domain experience highly relevant"
    ],
    "missing_requirements": [],
    "red_flags": [],
    "nice_to_have_matches": [
        "TypeScript (listed as nice to have)",
        "Docker experience (listed as plus)"
    ],
    "summary": "Strong frontend match; not a Special Match category so score stays at 8."
}}

EXAMPLE — Special Match, pure frontend in Leipzig (score 9.5–10):
{{
    "match_score": 9.5,
    "recommendation": "Yes",
    "special_match": true,
    "special_match_reasons": [
        "Pure frontend role with full React/TypeScript stack match",
        "Located in Leipzig"
    ],
    "what_youll_do": {{
        "matched": ["Build React/TypeScript UI", "Own frontend architecture", "Work with designers"],
        "unmatched": []
    }},
    "what_theyre_looking_for": {{
        "matched": ["React", "TypeScript", "5+ years frontend", "English"],
        "unmatched": []
    }},
    "match_reasons": [
        "Frontend-only role fully matches candidate stack",
        "Leipzig location is a strong extra fit"
    ],
    "missing_requirements": [],
    "red_flags": [],
    "nice_to_have_matches": ["Next.js"],
    "summary": "Special Match: pure frontend React/TypeScript role in Leipzig."
}}

**Notes**:
- Always fill what_youll_do and what_theyre_looking_for with concrete JD items
- Include "disqualification_reason" ONLY if score ≤ 3
- List "nice_to_have_matches" for bonus skills the candidate HAS
- special_match must be true for categories A/B/C; those scores must be 9–10, not 8
- **Never invent a city.** "Located in Leipzig" / Leipzig bonus ONLY when Location or the JD literally contains Leipzig (or Leipzig-Halle / 04103). Berlin, Munich, Hamburg, remote-Germany, etc. are NOT Leipzig.
"""
    return prompt


def analyze_job_with_ai(
    job: Dict,
    user_profile: str,
    criteria: str,
    temperature: float = 0.3,
    extract: Optional[Dict] = None,
) -> Optional[Dict]:
    """
    Use AI to analyze a job posting and determine match quality.
    
    Args:
        job: Job data dictionary
        user_profile: User's resume/profile
        criteria: Custom matching criteria
        temperature: Sampling temperature (eval runs often use 0.0 for stability)
        extract: Optional stack_gate extract to inject into the prompt
        
    Returns:
        AI analysis results as dictionary, or None if analysis fails
    """
    if not AI_API_KEY:
        print("Error: AI API key not configured in .env")
        return None
    
    prompt = build_matching_prompt(job, user_profile, criteria, extract=extract)
    
    try:
        # Using OpenAI API (compatible with OpenAI, Azure OpenAI, or OpenRouter)
        client = OpenAI(api_key=AI_API_KEY)
        
        response = client.chat.completions.create(
            model=AI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You are a strict professional career advisor specializing in job matching. German, backend-stack, and AI/ML-core gates already ran in code for this job. Parse each JD into 'what you'll do' and 'what they're looking for', then compare every item to the resume. Follow the Matching Criteria document in the user message exactly. Special Match A/B/C jobs must score 9–10 (not 8) with special_match=true. Never invent Leipzig or any other city — only claim Leipzig when Location or JD literally say Leipzig. Respond only with valid JSON."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=temperature,
            response_format={"type": "json_object"}  # Ensures JSON response
        )
        
        result = response.choices[0].message.content
        
        # Parse JSON response
        import json
        analysis = json.loads(result)
        finalized = _finalize_special_match(job, analysis)
        if extract:
            finalized["requirement_extract"] = extract
        finalized.setdefault("match_gate", "scored")
        return finalized
        
    except Exception as e:
        print(f"Error during AI analysis: {e}")
        return None


def evaluate_job(
    job: Dict,
    user_profile: str,
    criteria: str,
    temperature: float = 0.3,
) -> Optional[Dict]:
    """
    Full match path: German gate → AI/ML title → stack extract + local gate → score.

    Returns an analysis dict, or None if the scoring call fails after gates passed.
    """
    german_reason = find_mandatory_german_requirement(job)
    if german_reason:
        print("🚫 Mandatory German — skip remaining matching")
        print(f"   {german_reason}")
        analysis = german_disqualification_analysis(german_reason)
        analysis["match_gate"] = "german"
        return analysis

    ai_ml_title = find_ai_ml_title_requirement(job)
    if ai_ml_title:
        print("🚫 AI/ML-core title — skip remaining matching")
        print(f"   {ai_ml_title}")
        gate = {
            "kind": "ai_ml",
            "reason": ai_ml_title,
            "missing": ["AI/ML engineering experience (training/models/LLM stack)"],
        }
        return requirement_disqualification_analysis(gate)

    extract = extract_jd_requirements(job, temperature=0.0)
    if extract:
        gate = evaluate_requirement_gate(extract)
        if gate:
            print(
                f"🚫 Requirement gate ({gate.get('kind')}) — skip remaining matching"
            )
            print(f"   {gate.get('reason')}")
            return requirement_disqualification_analysis(gate, extract)
    else:
        print("⚠ Stack extract unavailable — fail-open to full scorer")

    return analyze_job_with_ai(
        job,
        user_profile,
        criteria,
        temperature=temperature,
        extract=extract,
    )


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return False


def _is_leipzig(job: Dict) -> bool:
    """True if location, title, or JD clearly places the job in Leipzig."""
    text = " ".join(
        [
            str(job.get("location") or ""),
            str(job.get("title") or ""),
            strip_html(str(job.get("description") or ""))[:4000],
        ]
    )
    if re.search(r"leipzig(?:\s*[-–]?\s*halle)?", text, re.I):
        return True
    # Leipzig urban postal codes (e.g. 04103); do not treat Berlin 10xxx as Leipzig.
    if re.search(r"\b041\d{2}\b", text):
        return True
    return False


_LEIPZIG_MENTION = re.compile(r"leipzig", re.I)


def _strip_false_leipzig_claims(analysis: Dict) -> bool:
    """
    Remove invented Leipzig mentions from AI output.
    Returns True if any Leipzig claim was removed.
    """
    removed = False

    for key in (
        "special_match_reasons",
        "match_reasons",
        "nice_to_have_matches",
        "missing_requirements",
        "red_flags",
    ):
        items = analysis.get(key) or []
        if not isinstance(items, list):
            continue
        cleaned = [item for item in items if not _LEIPZIG_MENTION.search(str(item))]
        if len(cleaned) != len(items):
            removed = True
            analysis[key] = cleaned

    summary = str(analysis.get("summary") or "")
    if _LEIPZIG_MENTION.search(summary):
        cleaned_summary = re.sub(
            r"[^.?!]*\b[Ll]eipzig\b[^.?!]*[.?!]?",
            " ",
            summary,
        )
        cleaned_summary = re.sub(r"\s{2,}", " ", cleaned_summary).strip()
        analysis["summary"] = cleaned_summary
        removed = True

    return removed


def _finalize_special_match(job: Dict, analysis: Dict) -> Dict:
    """Normalize special_match fields and apply Leipzig / special-match score floors."""
    special = _as_bool(analysis.get("special_match"))
    raw_reasons = analysis.get("special_match_reasons") or []
    if isinstance(raw_reasons, str):
        raw_reasons = [raw_reasons] if raw_reasons.strip() else []
    elif not isinstance(raw_reasons, list):
        raw_reasons = []
    reasons = [str(item).strip() for item in raw_reasons if str(item).strip()]
    analysis["special_match_reasons"] = reasons

    in_leipzig = _is_leipzig(job)
    if not in_leipzig:
        # Models often copy the Leipzig few-shot example onto Berlin/remote jobs.
        false_leipzig = _strip_false_leipzig_claims(analysis)
        reasons = [
            str(item).strip()
            for item in (analysis.get("special_match_reasons") or [])
            if str(item).strip()
        ]
    else:
        false_leipzig = False
        if special and not any("leipzig" in r.lower() for r in reasons):
            reasons.append("Located in Leipzig")
            analysis["special_match_reasons"] = reasons

    try:
        score = float(analysis.get("match_score") or 0)
    except (TypeError, ValueError):
        score = 0.0

    if special:
        floor = 9.5 if in_leipzig else 9.0
        if score < floor:
            score = floor
        # If AI invented a Leipzig bonus on a non-Leipzig job, drop the +0.5 band.
        if false_leipzig and not in_leipzig and score >= 9.5:
            score = 9.0
        score = min(score, 10.0)
        if analysis.get("recommendation") == "No":
            analysis["recommendation"] = "Yes"

    analysis["special_match"] = special
    analysis["special_match_reasons"] = reasons
    analysis["match_score"] = score
    return analysis


def _normalize_breakdown(section) -> Dict:
    """Coerce AI breakdown into {matched: [...], unmatched: [...]} lists."""
    if not isinstance(section, dict):
        return {"matched": [], "unmatched": []}
    matched = section.get("matched") or []
    unmatched = section.get("unmatched") or []
    return {
        "matched": [str(item).strip() for item in matched if str(item).strip()],
        "unmatched": [str(item).strip() for item in unmatched if str(item).strip()],
    }


def _analysis_fields(analysis: Dict) -> Dict:
    """Normalize AI analysis into the fields stored on job documents."""
    reasons = analysis.get("special_match_reasons") or []
    if not isinstance(reasons, list):
        reasons = [str(reasons)] if reasons else []
    return {
        "recommendation": analysis.get("recommendation", ""),
        "special_match": _as_bool(analysis.get("special_match")),
        "special_match_reasons": [str(item).strip() for item in reasons if str(item).strip()],
        "disqualification_reason": analysis.get("disqualification_reason", ""),
        "match_reasons": analysis.get("match_reasons", []),
        "missing_requirements": analysis.get("missing_requirements", []),
        "red_flags": analysis.get("red_flags", []),
        "nice_to_have_matches": analysis.get("nice_to_have_matches", []),
        "summary": analysis.get("summary", ""),
        "what_youll_do": _normalize_breakdown(analysis.get("what_youll_do")),
        "what_theyre_looking_for": _normalize_breakdown(analysis.get("what_theyre_looking_for")),
        "match_gate": analysis.get("match_gate") or "",
        "requirement_extract": analysis.get("requirement_extract") or {},
    }


def save_matched_job(job: Dict, analysis: Dict) -> bool:
    """
    Save a matched job to the matched_jobs collection.
    
    Args:
        job: Original job data
        analysis: AI analysis results
        
    Returns:
        True if saved successfully, False otherwise
    """
    try:
        company = job.get("company", "")
        if is_blocked_match_company(company):
            print(
                f"✗ Blocked company ({company!r}) — "
                f"scored on jobs only, not written to matched_jobs"
            )
            return False

        matched_jobs = get_collection("matched_jobs")
        
        # Check if already exists (Indeed: also match legacy job_/sj_ prefixes)
        existing_query = {"job_id": job["job_id"], "source": job["source"]}
        if job.get("source") == "indeed":
            variants = indeed_job_id_variants(job["job_id"])
            if variants:
                existing_query = {"job_id": {"$in": variants}, "source": "indeed"}
        if matched_jobs.find_one(existing_query):
            print(f"Matched job {job['job_id']} already exists, skip")
            return False
        
        matched_job_data = {
            # Original job fields
            "title": job.get("title", ""),
            "company": company,
            "location": scrub_bullet_location(job.get("location", "")),
            "link": job.get("link", ""),
            "job_id": job.get("job_id", ""),
            "source": job.get("source", ""),
            "description": job.get("description", ""),
            "applicants": job.get("applicants", ""),
            
            # AI matching analysis
            "match_score": analysis.get("match_score", 0),
            **_analysis_fields(analysis),
            
            # Metadata
            "status": "pending",  # pending, applied, interview, rejected, offer, …
            # Local wall clock — same basis as Telegram / Web "today" filters
            "matched_at": datetime.now(),
            "applied_at": None,
            "application_timeline": [],
            "notes": "",
            "application_qa": [],
            "highlights": [],
        }
        
        matched_jobs.insert_one(matched_job_data)
        print(f"✓ Saved matched job: {job.get('title')} (score: {analysis.get('match_score')})")
        return True
        
    except Exception as e:
        print(f"Error saving matched job: {e}")
        return False


def process_new_jobs(limit: Optional[int] = None, source: Optional[str] = None):
    """
    Process new job listings and find matches using AI.
    
    Args:
        limit: Maximum number of jobs to process (None for all)
        source: Filter by source (indeed, linkedin, or None for all)
    """
    print("=== Starting AI Job Matching ===")
    
    # Load user data
    user_profile = load_user_profile()
    if not user_profile:
        print("Error: No user profile found. Create docs/user_profile.md first.")
        return
    
    criteria = load_matching_criteria()
    if not criteria:
        print("Warning: No matching criteria found. Using profile only.")
    
    # Get new jobs from database
    new_jobs = get_new_jobs(limit=limit, source=source)
    total_jobs = len(new_jobs)
    print(f"Found {total_jobs} new jobs to analyze")
    
    if total_jobs == 0:
        print("No new jobs to process.")
        return
    
    matched_count = 0
    processed_count = 0
    german_skip_count = 0
    stack_skip_count = 0
    ai_ml_skip_count = 0
    
    for i, job in enumerate(new_jobs, 1):
        print(f"\n--- Processing job {i}/{total_jobs} ---")
        print(f"Title: {job.get('title')}")
        print(f"Company: {job.get('company')}")
        print(f"Source: {job.get('source')}")

        if not is_usable_job_description(job.get("description", "")):
            print("⚠ Empty/missing job description — skip AI, mark failed")
            processed_count += 1
            mark_job_as_matched(
                job.get("job_id"),
                job.get("source"),
                match_score=0,
                analysis={
                    "recommendation": "No",
                    "special_match": False,
                    "special_match_reasons": [],
                    "disqualification_reason": "Missing job description",
                    "match_reasons": [],
                    "missing_requirements": [],
                    "red_flags": ["Empty or missing job description"],
                    "nice_to_have_matches": [],
                    "summary": (
                        "No usable job description scraped; "
                        "skipped AI matching to avoid hallucinated scores."
                    ),
                    "what_youll_do": {"matched": [], "unmatched": []},
                    "what_theyre_looking_for": {"matched": [], "unmatched": []},
                    "match_gate": "empty_desc",
                },
            )
            continue

        analysis = evaluate_job(job, user_profile, criteria)
        
        if not analysis:
            print("⚠ AI analysis failed, skip")
            # Still mark so the same job is not retried forever
            mark_job_as_matched(
                job.get("job_id"),
                job.get("source"),
                match_score=0,
                analysis={
                    "recommendation": "No",
                    "disqualification_reason": "AI analysis failed",
                    "summary": "AI analysis failed; skipped to avoid reprocessing.",
                    "match_gate": "score_failed",
                },
            )
            continue
        
        processed_count += 1
        match_score = analysis.get("match_score", 0)
        recommendation = analysis.get("recommendation", "")
        disqualification_reason = analysis.get("disqualification_reason", "")
        analysis_fields = _analysis_fields(analysis)
        match_gate = analysis.get("match_gate") or "scored"

        if match_gate == "german":
            german_skip_count += 1
            print("✗ Stopped at German gate; not sent to full scorer")
        elif match_gate == "stack":
            stack_skip_count += 1
            print("✗ Stopped at backend-stack gate; not sent to full scorer")
        elif match_gate == "ai_ml":
            ai_ml_skip_count += 1
            print("✗ Stopped at AI/ML-core gate; not sent to full scorer")
        
        print(f"Match score: {match_score}/10")
        print(f"Recommendation: {recommendation}")
        if match_gate:
            print(f"Gate: {match_gate}")
        if analysis.get("special_match"):
            reasons = analysis.get("special_match_reasons") or []
            extra = f" ({', '.join(reasons[:3])})" if reasons else ""
            print(f"★ Special Match{extra}")
        
        # Show disqualification reason if present
        if disqualification_reason:
            print(f"⚠️  Disqualification: {disqualification_reason}")
        
        # Show nice-to-have matches if present
        nice_to_have = analysis.get("nice_to_have_matches", [])
        if nice_to_have and len(nice_to_have) > 0:
            print(f"✨ Nice-to-have matches: {', '.join(nice_to_have[:3])}")

        looking = analysis_fields["what_theyre_looking_for"]
        doing = analysis_fields["what_youll_do"]
        print(
            f"What you'll do: {len(doing['matched'])} matched / {len(doing['unmatched'])} unmatched"
        )
        print(
            f"What they're looking for: {len(looking['matched'])} matched / {len(looking['unmatched'])} unmatched"
        )
        if looking["unmatched"]:
            preview = "; ".join(looking["unmatched"][:3])
            print(f"  gaps: {preview}")
        
        # High-score jobs also go to matched_jobs for the tracker
        if match_score >= MATCH_THRESHOLD:
            if save_matched_job(job, analysis):
                matched_count += 1
        else:
            print(f"✗ Score below threshold ({MATCH_THRESHOLD}), kept on jobs with AI reason")
        
        # Always write the AI result onto the original jobs document.
        # Stack-gate rejects stay on Unmatched as title + link stubs (JD dropped).
        mark_job_as_matched(
            job.get("job_id"),
            job.get("source"),
            match_score=match_score,
            analysis=analysis_fields,
            clear_description=(match_gate == "stack"),
        )
        if match_gate == "stack":
            print("✓ Saved stack reject on jobs (title + link; JD cleared)")
        else:
            print(f"✓ Saved AI analysis on jobs collection")
    
    print(f"\n=== Matching Complete ===")
    print(f"Processed: {processed_count}/{total_jobs}")
    print(f"German gate skipped: {german_skip_count}")
    print(f"Stack gate skipped: {stack_skip_count}")
    print(f"AI/ML gate skipped: {ai_ml_skip_count}")
    print(f"Matched: {matched_count}")
    print(f"Threshold: {MATCH_THRESHOLD}/10")


def main():
    """Main entry point for AI job matching."""
    global MATCH_THRESHOLD
    
    import argparse
    
    parser = argparse.ArgumentParser(description="AI-powered job matching system")
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=None,
        help="Limit number of jobs to process"
    )
    parser.add_argument(
        "--source",
        "-s",
        choices=["indeed", "linkedin"],
        default=None,
        help="Filter by job source"
    )
    parser.add_argument(
        "--threshold",
        "-t",
        type=float,
        default=MATCH_THRESHOLD,
        help="Minimum match score threshold (0-10)"
    )
    
    args = parser.parse_args()
    
    # Override threshold if specified
    MATCH_THRESHOLD = args.threshold
    
    init_db()
    process_new_jobs(limit=args.limit, source=args.source)


if __name__ == "__main__":
    main()
