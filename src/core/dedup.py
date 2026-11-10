"""
Cross-source duplicate detection (same role posted on several boards).

job_id dedup only works inside one source. The API sources (Arbeitnow,
Bundesagentur, Adzuna, company ATS boards) often list the same role that
LinkedIn / Indeed already saved, so before spending an AI call we compare a
normalised company + title against recent `jobs` / `matched_jobs` rows.

Only the API sources call this; the LinkedIn / Indeed scrapers are unchanged.
"""
from __future__ import annotations

import re

# "(m/w/d)", "(f/m/x)", "(all genders)", "(Mensch*)", "w/m/d" …
_GENDER_TAG = re.compile(
    r"[\(\[]?\s*(?:[mwfdx]\s*/\s*[mwfdx](?:\s*/\s*[mwfdx]{1,3})?|all\s+genders?|"
    r"gn\*?|mensch\*?|m\s*\|\s*w\s*\|\s*d|[mwfd]\s*\*)\s*[\)\]]?",
    re.IGNORECASE,
)
_NON_WORD = re.compile(r"[^a-z0-9äöüß+#]+")
_LEGAL_SUFFIXES = {
    "gmbh", "ag", "se", "kg", "co", "kgaa", "mbh", "ug", "ohg", "gbr", "ev",
    "inc", "llc", "ltd", "limited", "plc", "bv", "b", "v", "sa", "sas", "srl",
    "corp", "corporation", "company", "group", "holding", "technologies",
    "technology", "tech", "gmbh&co", "haftungsbeschränkt",
}


def normalize_title(title: str) -> str:
    text = _GENDER_TAG.sub(" ", str(title or "").lower())
    text = text.replace("full stack", "fullstack").replace("full-stack", "fullstack")
    text = text.replace("front end", "frontend").replace("front-end", "frontend")
    return " ".join(_NON_WORD.sub(" ", text).split())


def normalize_company(company: str) -> str:
    text = re.sub(r"\(.*?\)", " ", str(company or "").lower())
    words = [w for w in _NON_WORD.sub(" ", text).split() if w not in _LEGAL_SUFFIXES]
    return " ".join(words)


def dedup_key(title: str, company: str) -> str:
    return f"{normalize_company(company)}|{normalize_title(title)}"


def find_cross_source_duplicate(db, title: str, company: str, source: str) -> dict | None:
    """
    Return {collection, source, title} of a recent row with the same normalised
    company + title from a *different* source, or None.
    """
    core = normalize_company(company)
    want = normalize_title(title)
    if not core or not want:
        return None
    first = core.split()[0]
    if len(first) < 2:
        return None
    query = {
        "company": {"$regex": "^" + re.escape(first), "$options": "i"},
        "source": {"$ne": source},
    }
    projection = {"title": 1, "company": 1, "source": 1}
    for coll, sort_field in (("matched_jobs", "matched_at"), ("jobs", "created_at")):
        for row in db[coll].find(query, projection).sort(sort_field, -1).limit(300):
            if normalize_company(row.get("company")) != core:
                continue
            if normalize_title(row.get("title")) == want:
                return {
                    "collection": coll,
                    "source": row.get("source") or "",
                    "title": row.get("title") or "",
                }
    return None
