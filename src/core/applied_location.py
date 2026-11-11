"""Germany + Remote labels for the applied-location chart.

Pure functions (no Mongo). Tracker country chips live in core.country;
this module only decides the bubble name and whether a place belongs
on the Germany + Remote chart.
"""
from __future__ import annotations

import re


# Parenthetical work-mode tags Often appended by LinkedIn / Indeed
_LOC_MODE_RE = re.compile(
    r"\s*[\(\[]\s*(remote|hybrid|on[\s-]?site|hybrides?\s+arbeiten)\s*[\)\]]\s*$",
    re.IGNORECASE,
)

# "Pullach near Munich" → keep the specific place before "near"
_NEAR_PLACE_RE = re.compile(
    r"^\s*(.+?)\s+near\s+\S+",
    re.IGNORECASE,
)

# Country / region only (no city) → count as Remote
_COUNTRY_ONLY = {
    "germany",
    "deutschland",
    "de",
    "eu",
    "europe",
    "european union",
    "european union (remote)",
    "european economic area",
    "eea",
    "dach",
    "emea",
}

# German states / non-city admin regions (not bubble labels by themselves)
_REGION_ONLY = {
    "bavaria",
    "bayern",
    "saxony",
    "sachsen",
    "hesse",
    "hessen",
    "lower saxony",
    "niedersachsen",
    "baden-württemberg",
    "baden-wuerttemberg",
    "baden württemberg",
    "north rhine-westphalia",
    "nordrhein-westfalen",
    "nrw",
    "rhineland-palatinate",
    "rheinland-pfalz",
    "schleswig-holstein",
    "mecklenburg-vorpommern",
    "brandenburg",
    "thuringia",
    "thüringen",
    "thueringen",
    "saarland",
    "uk",
    "ireland",
    "united kingdom",
}

# Known city aliases → canonical display name (lowercase keys)
_CITY_CANON = {
    "berlin": "Berlin",
    "berlin-kreuzberg": "Berlin",
    "kreuzberg": "Berlin",
    "munich": "Munich",
    "muenchen": "Munich",
    "münchen": "Munich",
    "hamburg": "Hamburg",
    "leipzig": "Leipzig",
    "cologne": "Cologne",
    "köln": "Cologne",
    "koeln": "Cologne",
    "frankfurt": "Frankfurt",
    "frankfurt am main": "Frankfurt",
    "stuttgart": "Stuttgart",
    "dresden": "Dresden",
    "nuremberg": "Nuremberg",
    "nürnberg": "Nuremberg",
    "nuernberg": "Nuremberg",
    "hannover": "Hannover",
    "hanover": "Hannover",
    "dortmund": "Dortmund",
    "augsburg": "Augsburg",
    "freiburg": "Freiburg",
    "ravensburg": "Ravensburg",
    "kronberg": "Kronberg",
    "böblingen": "Böblingen",
    "boeblingen": "Böblingen",
    "neu-ulm": "Neu-Ulm",
    "ulm": "Ulm",
    "mannheim": "Mannheim",
    "karlsruhe": "Karlsruhe",
    "heidelberg": "Heidelberg",
    "bonn": "Bonn",
    "düsseldorf": "Düsseldorf",
    "duesseldorf": "Düsseldorf",
    "dusseldorf": "Düsseldorf",
    "essen": "Essen",
    "bremen": "Bremen",
    "potsdam": "Potsdam",
    "wiesbaden": "Wiesbaden",
    "mainz": "Mainz",
    "kassel": "Kassel",
    "wuppertal": "Wuppertal",
}

# Longer aliases first so "berlin-kreuzberg" wins over "berlin"
_CITY_ALIASES_BY_LEN = tuple(
    sorted(_CITY_CANON.items(), key=lambda item: len(item[0]), reverse=True)
)


def _title_place(name: str) -> str:
    """Title-case a free-form place while keeping hyphenated parts."""
    parts = []
    for chunk in name.strip().split("-"):
        words = [w.capitalize() if w else w for w in chunk.split(" ")]
        parts.append(" ".join(words))
    return "-".join(parts)


def _is_country_or_region(token: str) -> bool:
    return token in _COUNTRY_ONLY or token in _REGION_ONLY


def _city_in_text(text_lower: str) -> str | None:
    """Return canonical city if a known alias appears as a whole word/token."""
    for alias, canon in _CITY_ALIASES_BY_LEN:
        if re.search(
            rf"(?<![a-z0-9äöüß]){re.escape(alias)}(?![a-z0-9äöüß])",
            text_lower,
        ):
            return canon
    return None


# Non-German countries / cities: the location chart is Germany + Remote only
_FOREIGN_MARKERS = (
    "netherlands", "the netherlands", "holland", "amsterdam", "rotterdam",
    "the hague", "den haag", "utrecht", "eindhoven", "geleen", "delft",
    "sweden", "stockholm", "gothenburg", "malmö", "malmo",
    "united kingdom", "uk", "england", "scotland", "london", "manchester",
    "edinburgh", "cambridge", "oxford",
    "ireland", "dublin", "cork",
    "france", "paris", "lyon", "spain", "madrid", "barcelona", "valencia",
    "portugal", "lisbon", "porto", "italy", "milan", "rome",
    "austria", "vienna", "wien", "graz", "switzerland", "zurich", "zürich",
    "geneva", "basel", "lausanne",
    "belgium", "brussels", "antwerp", "luxembourg",
    "denmark", "copenhagen", "norway", "oslo", "finland", "helsinki",
    "poland", "warsaw", "krakow", "kraków", "wroclaw", "wrocław",
    "czech republic", "czechia", "prague", "hungary", "budapest",
    "romania", "bucharest", "estonia", "tallinn", "lithuania", "vilnius",
    "latvia", "riga", "greece", "athens", "croatia", "zagreb",
    "united states", "usa", "us", "new york", "san francisco", "canada",
    "toronto", "india", "bangalore", "singapore", "dubai", "israel", "tel aviv",
)
_FOREIGN_RE = re.compile(
    r"(?<![a-z0-9äöüß])(?:"
    + "|".join(re.escape(m) for m in sorted(_FOREIGN_MARKERS, key=len, reverse=True))
    + r")(?![a-z0-9äöüß])"
)
_GERMAN_CITY_NAMES = frozenset(_CITY_CANON.values())


def is_germany_or_remote(raw: str, normalized: str | None = None) -> bool:
    """
    True when an applied location belongs on the Germany + Remote chart.

    A known German city always counts (e.g. "Stuttgart, Ulm or Berlin").
    Otherwise any non-German country / city marker excludes it, including
    country-scoped remote like "Remote, UK" — unless Germany is also named.
    Unknown localities without a
    foreign marker (Manching, Gilching, …) are treated as German towns.
    """
    name = normalized if normalized is not None else normalize_applied_location(raw)
    if name in _GERMAN_CITY_NAMES:
        return True
    text = (raw or "").lower()
    # Multi-country postings that include Germany ("Remote - Germany | UK") stay
    if re.search(r"(?<![a-z])(germany|deutschland)(?![a-z])", text):
        return True
    return not _FOREIGN_RE.search(text)


def normalize_applied_location(raw: str) -> str:
    """
    Normalize a job location for applied-job bubble charts.

    Rules:
      - Pure remote / home-office / country-only labels → Remote
      - A concrete city wins over work-mode tags like (Remote)/(Hybrid)
      - Street / postal lines that mention a known city (e.g. \"10407 Berlin\")
        count as that city, not Remote
      - \"X near City\" keeps the specific place X (does not fold into City)
      - Other named localities become their own bubble label
      - Work-mode suffixes are ignored for place detection
    """
    text = (raw or "").strip()
    if not text:
        return "Remote"

    lower = text.lower().strip()

    # Pure remote labels (any casing)
    if lower in {"remote", "homeoffice", "home office", "fully remote", "100% remote"}:
        return "Remote"
    if lower.startswith("remote ") or lower.startswith("remote-") or lower.startswith("remote/"):
        return "Remote"

    # Drop trailing work-mode tag for city / country detection
    cleaned = _LOC_MODE_RE.sub("", text).strip(" ,;-|")
    cleaned_lower = cleaned.lower()

    if not cleaned_lower or cleaned_lower in _COUNTRY_ONLY:
        return "Remote"

    # "Greater Munich Metropolitan Area", "Frankfurt Rhine-Main…"
    for key, canon in (
        ("munich", "Munich"),
        ("münchen", "Munich"),
        ("muenchen", "Munich"),
        ("frankfurt", "Frankfurt"),
        ("berlin", "Berlin"),
        ("hamburg", "Hamburg"),
        ("leipzig", "Leipzig"),
        ("cologne", "Cologne"),
        ("köln", "Cologne"),
        ("stuttgart", "Stuttgart"),
    ):
        if key in cleaned_lower and (
            "metropolitan" in cleaned_lower
            or "greater" in cleaned_lower
            or "rhine" in cleaned_lower
            or "area" in cleaned_lower
        ):
            return canon

    # "Pullach near Munich" → Pullach (specific place, not the nearby big city)
    near_match = _NEAR_PLACE_RE.match(cleaned)
    if near_match:
        specific = re.sub(r"\s+", " ", near_match.group(1).strip())
        specific_lower = specific.lower()
        if specific_lower in _CITY_CANON:
            return _CITY_CANON[specific_lower]
        if specific_lower and not _is_country_or_region(specific_lower):
            return _title_place(specific)

    # Scan comma / slash segments left-to-right for the first known city.
    # Also match city tokens anywhere in a segment so street/postal lines work:
    # "Kastanienallee 97, 10435 Berlin" / "10407 Berlin".
    segments = re.split(r"[,|/•·]|\bor\b", cleaned, flags=re.IGNORECASE)
    found = []
    for seg in segments:
        token = re.sub(r"\s+", " ", seg.strip().lower())
        if not token or _is_country_or_region(token):
            continue
        if token in _CITY_CANON:
            found.append(_CITY_CANON[token])
            continue
        # Whole-word city anywhere in the segment (street + PLZ + city)
        hit = _city_in_text(token)
        if hit:
            found.append(hit)
            continue
        for alias, canon in _CITY_ALIASES_BY_LEN:
            if token.startswith(alias + " ") or token.startswith(alias + "-"):
                found.append(canon)
                break

    if found:
        # Multiple distinct cities in one posting → keep first (primary)
        return found[0]

    # No concrete city, but the label still says remote (e.g. "European Remote")
    if re.search(r"(?<![a-z0-9])remote(?![a-z0-9])", cleaned_lower):
        return "Remote"

    # Named locality without a known-city alias → keep that place as its own bubble
    for seg in segments:
        token = re.sub(r"\s+", " ", seg.strip())
        if not token:
            continue
        token_lower = token.lower()
        if _is_country_or_region(token_lower):
            continue
        # Skip pure street lines with house numbers but no remaining place word
        if re.fullmatch(r"[\w.\-äöüÄÖÜß]+\s+\d+[a-zA-Z]?", token):
            continue
        return _title_place(token)

    return "Remote"
