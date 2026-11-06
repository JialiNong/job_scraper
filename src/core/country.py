"""
Tracker country for a job.

A job's country comes from an explicit `country` field (agents send it on
POST /api/agent/jobs) or, when absent, is derived from the free-text
location. Locations that match no pattern count as Germany, since the
scrapers search Germany and bare German city names carry no country.

Kept free of Flask / Mongo clients so it can be unit-tested directly.
"""
from __future__ import annotations

import re
from typing import Optional

REMOTE = "remote"
OTHER = "other"
DEFAULT_COUNTRY = "germany"

# (key, label, location regex). Checked in order, first hit wins, so a
# concrete country beats a "Remote" work-mode tag ("Remote (Germany)" is
# Germany) and Germany beats any other country it is listed with. Remote stays last and only matches a location that *starts*
# with a remote / region word.
COUNTRIES: tuple[tuple[str, str, str], ...] = (
    # Germany first: "Berlin (hybrid) or Helsinki" is open in Germany.
    ("germany", "Germany",
     r"germany|deutschland|\bberlin\b|munich|m[uü]nchen|hamburg|frankfurt"
     r"|cologne|k[oö]ln|stuttgart|d[uü]sseldorf|leipzig|dresden|hannover|hanover"
     r"|nuremberg|n[uü]rnberg|\bbonn\b|karlsruhe|mannheim|heidelberg|freiburg"
     r"|m[uü]nster|aachen|bremen|potsdam|erlangen|darmstadt|wiesbaden|mainz"
     r"|augsburg|regensburg|dortmund|\bessen\b|bochum|duisburg|bielefeld|\bkiel\b|rostock"),
    ("netherlands", "Netherlands",
     r"netherlands|nederland|holland|amsterdam|rotterdam|utrecht"
     r"|eindhoven|the hague|den haag|delft|leiden|groningen|geleen"
     r"|haarlem|amstelveen|hoofddorp|almere|tilburg|breda|nijmegen"
     r"|arnhem|maastricht|zwolle|enschede"),
    ("austria", "Austria", r"austria|österreich|osterreich|\bvienna\b|\bwien\b|\bgraz\b|salzburg|innsbruck"),
    ("switzerland", "Switzerland",
     r"switzerland|schweiz|suisse|z[uü]rich|geneva|gen[eè]ve|\bbasel\b|\bbern\b|lausanne|\bzug\b|lugano"),
    ("france", "France", r"france|\bparis\b|\blyon\b|toulouse|marseille|bordeaux|\blille\b|nantes"),
    ("belgium", "Belgium", r"belgium|belgi[eë]|belgique|brussels|bruxelles|brussel|antwerp|antwerpen|\bghent\b|\bgent\b|leuven"),
    ("luxembourg", "Luxembourg", r"luxembourg|luxemburg"),
    ("spain", "Spain", r"spain|españa|espana|madrid|barcelona|valencia|m[aá]laga|seville|sevilla|bilbao"),
    ("portugal", "Portugal", r"portugal|lisbon|lisboa|\bporto\b|braga"),
    ("italy", "Italy", r"italy|italia|\bmilan\b|milano|\brome\b|\broma\b|turin|torino|bologna"),
    ("uk", "United Kingdom",
     r"united kingdom|\bu\.?k\.?\b|great britain|england|scotland|wales|london"
     r"|manchester|edinburgh|glasgow|bristol|cambridge|oxford|leeds|birmingham"),
    ("ireland", "Ireland", r"ireland|dublin|\bcork\b|galway"),
    ("denmark", "Denmark", r"denmark|danmark|copenhagen|københavn|kobenhavn|aarhus"),
    ("sweden", "Sweden", r"sweden|sverige|stockholm|gothenburg|göteborg|goteborg|malmö|malmo|\blund\b|uppsala"),
    ("norway", "Norway", r"norway|norge|\boslo\b|bergen|trondheim|stavanger"),
    ("finland", "Finland", r"finland|suomi|helsinki|espoo|tampere|\boulu\b|turku|vantaa"),
    ("iceland", "Iceland", r"iceland|reykjav[ií]k"),
    ("estonia", "Estonia", r"estonia|eesti|tallinn|tartu"),
    ("latvia", "Latvia", r"latvia|\briga\b"),
    ("lithuania", "Lithuania", r"lithuania|vilnius|kaunas"),
    ("poland", "Poland", r"poland|polska|warsaw|warszawa|krak[oó]w|cracow|wroc[lł]aw|gda[nń]sk|pozna[nń]|\b[lł][oó]d[zź]\b"),
    ("czechia", "Czechia", r"czech|česko|prague|praha|\bbrno\b"),
    ("slovakia", "Slovakia", r"slovakia|bratislava"),
    ("hungary", "Hungary", r"hungary|budapest"),
    ("romania", "Romania", r"romania|bucharest|bucure[sș]ti|cluj"),
    ("bulgaria", "Bulgaria", r"bulgaria|sofia"),
    ("greece", "Greece", r"greece|athens|thessaloniki"),
    ("croatia", "Croatia", r"croatia|zagreb"),
    ("slovenia", "Slovenia", r"slovenia|ljubljana"),
    ("serbia", "Serbia", r"serbia|belgrade|novi sad"),
    ("malta", "Malta", r"malta|valletta"),
    ("cyprus", "Cyprus", r"cyprus|limassol|nicosia"),
    (OTHER, "Outside Europe",
     r"united states|\busa?\b|canada|toronto|vancouver|new york|san francisco"
     r"|india|bangalore|singapore|australia|sydney|brazil|mexico|israel|tel aviv"
     r"|dubai|\buae\b|japan|tokyo|china|hong kong|south africa|argentina|turkey|istanbul"),
    (REMOTE, "Remote",
     r"^\s*(fully\s+)?(remote|worldwide|global|anywhere|european union"
     r"|european economic area|emea|europe|eu)\b"),
)

COUNTRY_LABELS = {key: label for key, label, _ in COUNTRIES}
_KEYS = [key for key, _, _ in COUNTRIES]

# Extra spellings an agent may send in the explicit `country` field.
_COUNTRY_ALIASES = {
    "de": "germany", "nl": "netherlands", "at": "austria", "ch": "switzerland",
    "fr": "france", "be": "belgium", "lu": "luxembourg", "es": "spain",
    "pt": "portugal", "it": "italy", "gb": "uk", "uk": "uk", "ie": "ireland",
    "dk": "denmark", "se": "sweden", "no": "norway", "fi": "finland",
    "is": "iceland", "ee": "estonia", "lv": "latvia", "lt": "lithuania",
    "pl": "poland", "cz": "czechia", "sk": "slovakia", "hu": "hungary",
    "ro": "romania", "bg": "bulgaria", "gr": "greece", "hr": "croatia",
    "si": "slovenia", "rs": "serbia", "mt": "malta", "cy": "cyprus",
    "czech republic": "czechia", "the netherlands": "netherlands",
    "remote": REMOTE, "global remote": REMOTE, "remote (global)": REMOTE,
    "remote (europe)": REMOTE, "remote (eu)": REMOTE, "worldwide": REMOTE,
    "europe": REMOTE, "eu": REMOTE, "emea": REMOTE, "anywhere": REMOTE,
    "outside europe": OTHER, "other": OTHER,
}


def country_from_location(location) -> Optional[str]:
    """Country key whose pattern matches the location, or None."""
    text = str(location or "")
    if not text.strip():
        return None
    for key, _, pattern in COUNTRIES:
        if re.search(pattern, text, re.IGNORECASE):
            return key
    return None


def normalize_country(value) -> str:
    """
    Map an agent-supplied country (key, name, ISO code, or a location-like
    string such as "Helsinki, Finland") to a stored key; '' when unknown.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    low = text.lower()
    if low in COUNTRY_LABELS:
        return low
    for key, label in COUNTRY_LABELS.items():
        if low == label.lower():
            return key
    if low in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[low]
    return country_from_location(text) or ""


def job_country(location, stored=None) -> str:
    """Tracker country key: stored field first, then location, then Germany."""
    key = str(stored or "").strip().lower()
    if key in COUNTRY_LABELS:
        return key
    return country_from_location(location) or DEFAULT_COUNTRY


# Tracker filter groups (the dropdown only offers these, plus "all").
EUROPE_EXCEPT_GERMANY = tuple(
    k for k in COUNTRY_LABELS if k not in (DEFAULT_COUNTRY, REMOTE, OTHER)
)
FILTER_GROUPS = {
    "germany": (DEFAULT_COUNTRY,),
    "europe": EUROPE_EXCEPT_GERMANY,
    "remote": (REMOTE,),
}
FILTER_LABELS = {
    "germany": "Germany",
    "europe": "Europe (excl. Germany)",
    "remote": "Remote (global)",
}


def _no_match(pattern):
    return {"location": {"$not": re.compile(pattern, re.IGNORECASE)}}


def _derived_query(key: str) -> dict:
    """Mongo clause: country_from_location(location) == key (Germany = fallback too)."""
    idx = _KEYS.index(key)
    clauses = [_no_match(p) for _, _, p in COUNTRIES[:idx]]
    hit = {"location": {"$regex": COUNTRIES[idx][2], "$options": "i"}}
    if key == DEFAULT_COUNTRY:
        hit = {"$or": [hit, {"$and": [_no_match(p) for _, _, p in COUNTRIES[idx + 1:]]}]}
    clauses.append(hit)
    return {"$and": clauses}


def country_filter_query(group: Optional[str]) -> dict:
    """
    Mongo clause for a Tracker filter group (germany / europe / remote),
    or a single country key; {} for "all" / unknown.
    Matches job_country(location, country) in the group.
    """
    if group in FILTER_GROUPS:
        keys = FILTER_GROUPS[group]
    elif group in COUNTRY_LABELS:
        keys = (group,)
    else:
        return {}
    no_stored = {"country": {"$nin": list(COUNTRY_LABELS)}}
    derived = [_derived_query(k) for k in keys]
    derived_clause = derived[0] if len(derived) == 1 else {"$or": derived}
    return {"$or": [{"country": {"$in": list(keys)}}, {"$and": [no_stored, derived_clause]}]}
