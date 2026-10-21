"""
Pre-AI gate: English JDs that explicitly require German as a job language.

If this hits, matching should stop — no stack / years / domain scoring.
Do not treat location, office, company, or market in Germany as a language requirement.
Do not treat German-OR-English alternatives, nice-to-have, plus, or basic German as mandatory.
Do not treat "businesses speak German" / since-clause explanations as a candidate requirement.
"""
import html as html_module
import re
from typing import Dict, Optional, Tuple

# Language skill "German" / "Deutsch" — not "Germany".
_GERMAN_SKILL = re.compile(
    r"\b(?:german|deutsch\w*)\b",
    re.I,
)

_LANG_DE = r"(?:german|deutsch\w*)"
_LANG_EN = r"(?:english|englisch\w*)"
_LEVEL_WORD = (
    r"(?:fluent(?:ly)?|flie[sß]end(?:e[sn]?)?|native|sehr\s+gute|"
    r"very\s+good|excellent|good)"
)
# "and/or" is an alternative, not AND.
_OR_CONN = r"(?:and/?\s*or|/|(?:-|\s)\s*oder\b|\bor\b)"
_AND_CONN = r"(?:and(?!/?\s*or)|&|\bund\b)"

# Nearby text that means German is being asked as a language skill.
_LANGUAGE_CONTEXT = re.compile(
    r"\b(?:"
    r"language|languages|speak|speaking|speaker|spoken|"
    r"fluency|fluent|native|proficiency|proficient|"
    r"level|kenntnisse|skills?|written|oral|verbal|"
    r"c1|c2|b1|b2|a2|"
    r"muttersprach|flie[sß]end|verhandlungssicher"
    r")\b",
    re.I,
)

# German as an alternative to English (either language is enough).
_OR_ENGLISH = re.compile(
    rf"(?:"
    rf"{_LANG_DE}-?\s*{_OR_CONN}\s*(?:{_LEVEL_WORD}\s+)?{_LANG_EN}"
    rf"|"
    rf"{_LANG_EN}-?\s*{_OR_CONN}\s*(?:{_LEVEL_WORD}\s+)?{_LANG_DE}"
    rf"|"
    rf"{_LANG_DE}(?:kenntnisse)?(?:\s+(?:skills?|kenntnisse|language|languages))?\s+"
    rf"{_OR_CONN}\s+(?:{_LEVEL_WORD}\s+)?{_LANG_EN}"
    rf"|"
    rf"{_LANG_EN}(?:kenntnisse)?(?:\s+(?:skills?|kenntnisse|language|languages))?\s+"
    rf"{_OR_CONN}\s+(?:{_LEVEL_WORD}\s+)?{_LANG_DE}"
    rf")",
    re.I,
)

# Both German and English (AND, not OR).
_AND_ENGLISH = re.compile(
    rf"(?:"
    rf"{_LANG_DE}(?:kenntnisse)?(?:\s+(?:skills?|kenntnisse|language|languages))?\s+"
    rf"{_AND_CONN}\s+(?:{_LEVEL_WORD}\s+)?{_LANG_EN}"
    rf"|"
    rf"{_LANG_EN}(?:kenntnisse)?(?:\s+(?:skills?|kenntnisse|language|languages))?\s+"
    rf"{_AND_CONN}\s+(?:{_LEVEL_WORD}\s+)?{_LANG_DE}"
    rf")",
    re.I,
)

# Fluency / CEFR attached to German itself — not "fluent English" in the same window.
_GERMAN_LEVEL_ATTACHED = re.compile(
    r"(?:"
    r"(?:fluent(?:ly)?|fluency|native|proficient|proficiency|"
    r"business[- ]fluent|very good|excellent|"
    r"c1|c2|b2|b1|"
    r"flie[sß]end(?:e[sn]?)?|muttersprach\w*|verhandlungssicher|"
    r"sehr gute|ausgezeichnete)\s+"
    r"(?:in\s+)?(?:german|deutsch\w*)"
    r"|"
    r"(?:german|deutsch\w*)(?:kenntnisse)?(?:\s+(?:skills?|language))?\s*"
    r"(?:level\s*)?(?:c1|c2|b2|b1)"
    r"|"
    r"(?:c1|c2|b2|b1)\s+(?:in\s+)?(?:german|deutsch\w*)"
    r"|"
    r"(?:german|deutsch\w*)\s*[:\-–]?\s*(?:c1|c2|b2)"
    r"|"
    r"deutsch(?:kenntnisse)?\s*[:\-–]?\s*"
    r"(?:flie[sß]end|c1|c2|b2|muttersprach|erforderlich|verhandlungssicher)"
    r")",
    re.I,
)

# Required / must-have attached to German, not to English in the same window.
_GERMAN_REQUIRED_ATTACHED = re.compile(
    r"(?:"
    r"(?<!no )(?:german|deutsch\w*)(?:kenntnisse)?(?:\s+(?:skills?|language|kenntnisse|c1|c2|b2))?\s*"
    r"(?:is\s+|are\s+)?(?<!not )(?:a\s+must|required|mandatory|essential|necessary|"
    r"erforderlich|zwingend|notwendig)"
    r"|"
    r"(?:required|mandatory|essential|must[- ]have|erforderlich|a\s+must)\s*[:\-–]?\s*"
    r"(?:[^.]{0,48}?)(?:german|deutsch\w*)"
    r"|"
    r"must\s+(?:speak|have|be)\s+(?:fluent(?:ly)?\s+)(?:german|deutsch)"
    r"|"
    r"must\s+(?:speak|have|be)\s+(?:german|deutsch)"
    r"|"
    r"(?:we require|you must)\s+(?:[^.]{0,40}?)(?:german|deutsch)"
    r")",
    re.I,
)

_AND_BOTH_REQUIRED = re.compile(
    r"\b(?:"
    r"required|requirement|mandatory|must[- ]have|must have|a\s+must|"
    r"essential|necessary|erforderlich|zwingend|notwendig|"
    r"skills?|kenntnisse|language|languages"
    r")\b",
    re.I,
)

# Nice-to-have / plus — qualifier may sit before or after the German mention.
_OPTIONAL = re.compile(
    r"(?:"
    r"\b(?:nice[- ]to[- ]have|advantageous|advantage|preferred|preferably|"
    r"beneficial|desirable|helpful|bonus|ideally|optional|welcome|"
    r"not required|not mandatory|"
    r"would be great|would be a plus|"
    r"(?:is\s+)?a\s+plus|(?:is\s+)?a\s+bonus|"
    r"basic|basics|beginner|"
    r"grundkenntnisse|"
    r"von vorteil|vorteil|"
    r"w[uü]nschenswert|idealerweise|"
    r"ein plus|gerne)\b|"
    r"\bno\s+(?:german|deutsch\w*)\s+required\b"
    r")",
    re.I,
)

# "German office / customers / market / company" is not a language skill.
_NOT_LANGUAGE = re.compile(
    r"\bgerman\s+(?:office|offices|hq|headquarters|market|markets|"
    r"customer|customers|client|clients|brand|brands|"
    r"company|companies|startup|team|engineering team|"
    r"public[- ]transport|gym|perk)\b",
    re.I,
)

# Clients/businesses speaking German is context, not a candidate must-have.
_OTHERS_SPEAK_GERMAN = re.compile(
    r"\b(?:business(?:es)?|compan(?:y|ies)|customers?|clients?|"
    r"stakeholders?|users?|partners?|teams?)\b"
    r".{0,80}\bspeak(?:s|ing)?\s+(?:german|deutsch)",
    re.I,
)

# Split a sentence into main clause vs since/because explanation.
_CLAUSE_SPLIT = re.compile(
    r"\s*(?:,\s*)?(?:\bsince\b|\bbecause\b|\bweil\b|\bdenn\b|\bda\b)\s+",
    re.I,
)

_WINDOW = 110


def _plain_text(html_text: str) -> str:
    if not html_text:
        return ""
    text = re.sub(
        r"<(style|script)[^>]*>.*?</\1>",
        " ",
        html_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    return " ".join(text.split())


def _sentence_bounds(text: str, pos: int) -> Tuple[int, int]:
    # ";" ends a clause too: "Fluent English required; German is a plus"
    # must not let "required" reach across to "German".
    lo = 0
    for match in re.finditer(r"[.!?;]", text[:pos]):
        lo = match.end()
    hi = len(text)
    match = re.search(r"[.!?;]", text[pos:])
    if match:
        hi = pos + match.end()
    return lo, hi


def _clause_at(text: str, pos: int) -> Tuple[str, bool]:
    """
    Clause containing pos, and whether it is a since/because subordinate.

    Qualifiers like 'is a plus' are judged on this clause so a later
    'since businesses speak German' cannot turn a plus into a requirement,
    and cannot hide 'fluent German required' in a previous sentence.
    """
    sent_lo, sent_hi = _sentence_bounds(text, pos)
    sentence = text[sent_lo:sent_hi]
    rel = pos - sent_lo
    clause_lo = 0
    clause_hi = len(sentence)
    subordinate = False
    for match in _CLAUSE_SPLIT.finditer(sentence):
        if match.start() < rel:
            clause_lo = match.end()
            subordinate = True
        else:
            clause_hi = match.start()
            break
    return sentence[clause_lo:clause_hi].strip(), subordinate


def _segment_at(clause: str, rel: int) -> str:
    """Comma-separated piece of clause around rel (German's own qualifier)."""
    lo = clause.rfind(",", 0, rel) + 1
    hi = clause.find(",", rel)
    if hi < 0:
        hi = len(clause)
    return clause[lo:hi].strip()


def _snippet(text: str, start: int, end: int, pad: int = 40) -> str:
    lo = max(0, start - pad)
    hi = min(len(text), end + pad)
    chunk = " ".join(text[lo:hi].split())
    if lo > 0:
        chunk = "…" + chunk
    if hi < len(text):
        chunk = chunk + "…"
    return chunk[:180]


def _reason(text: str, start: int, end: int) -> str:
    snippet = _snippet(text, start, end)
    return (
        "Mandatory German language requirement "
        f"(candidate does not speak German): \"{snippet}\""
    )


def find_mandatory_german_requirement(job: Dict) -> Optional[str]:
    """
    Return a short disqualification reason if the JD explicitly requires
    German as a job language (must-have / fluent / native / CEFR level).

    Returns None when German is absent, nice-to-have, plus, basic, an English
    alternative (German or English), only appears as location / office /
    company / market, or is only mentioned as a language others speak.
    """
    raw = " ".join(
        str(part or "")
        for part in (
            job.get("title"),
            job.get("location"),
            job.get("description"),
        )
    )
    text = _plain_text(raw)
    if not text or not _GERMAN_SKILL.search(text):
        return None

    for match in _GERMAN_SKILL.finditer(text):
        start, end = match.start(), match.end()
        window = text[max(0, start - _WINDOW) : min(len(text), end + _WINDOW)]
        clause, subordinate = _clause_at(text, start)

        if _NOT_LANGUAGE.search(window):
            if not _LANGUAGE_CONTEXT.search(window):
                continue

        if _OTHERS_SPEAK_GERMAN.search(clause) or _OTHERS_SPEAK_GERMAN.search(window):
            continue

        optional_hit = bool(_OPTIONAL.search(clause))
        required_hit = bool(_GERMAN_REQUIRED_ATTACHED.search(clause))
        level_hit = bool(_GERMAN_LEVEL_ATTACHED.search(clause))

        # German's own comma piece says plus / nice-to-have: "Fluent English
        # required, German is a plus". A "required" that belongs to English
        # in an earlier piece must not override it.
        rel = clause.lower().find(match.group(0).lower())
        segment = _segment_at(clause, rel) if rel >= 0 else clause
        if _OPTIONAL.search(segment) and not _GERMAN_REQUIRED_ATTACHED.search(segment):
            continue

        # Plus / nice-to-have in this clause wins even if the same clause
        # says "working proficiency in German". "Fluent German required.
        # English is a plus" still fails: plus is in a different sentence.
        if optional_hit and not required_hit:
            continue

        # Explanatory since/because text is not a candidate must-have
        # unless that clause itself uses required/must.
        if subordinate and not required_hit:
            continue

        # "German or English" / "Deutsch- oder Englischkenntnisse".
        if _OR_ENGLISH.search(clause) or _OR_ENGLISH.search(window):
            continue

        # "German and English required" — both languages, not an alternative.
        and_scope = clause or window
        if _AND_ENGLISH.search(and_scope) and _AND_BOTH_REQUIRED.search(and_scope):
            return _reason(text, start, end)

        if level_hit or required_hit:
            return _reason(text, start, end)

        # Strong collocations even without a separate "language" word.
        strong = re.search(
            r"(?:"
            r"fluent(?:ly)?\s+in\s+german|"
            r"german\s+(?:language\s+)?(?:skills?\s+)?(?:required|mandatory|essential|necessary)|"
            r"must\s+(?:speak|have|be)\s+german|"
            r"(?:working\s+)?languages?\s+(?:is|are)\s+german|"
            r"german\s+as\s+(?:a\s+)?(?:working\s+)?language|"
            r"deutsch(?:kenntnisse)?\s*[:\-–]?\s*(?:flie[sß]end|c1|c2|b2|muttersprach|erforderlich)"
            r")",
            clause or window,
            re.I,
        )
        if strong:
            return _reason(text, start, end)

    return None


def german_disqualification_analysis(reason: str) -> Dict:
    """Minimal analysis payload — no stack / years scoring."""
    return {
        "match_score": 1.0,
        "recommendation": "No",
        "special_match": False,
        "special_match_reasons": [],
        "disqualification_reason": reason,
        "what_youll_do": {"matched": [], "unmatched": []},
        "what_theyre_looking_for": {
            "matched": [],
            "unmatched": ["German as a mandatory job-language requirement"],
        },
        "match_reasons": [],
        "missing_requirements": ["German (mandatory job language)"],
        "red_flags": ["Mandatory German language requirement — skipped remaining matching"],
        "nice_to_have_matches": [],
        "summary": "Stopped at German language gate; other requirements were not evaluated.",
    }
