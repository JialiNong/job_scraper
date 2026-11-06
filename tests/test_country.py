"""Tracker country: explicit agent field, location guess, and Mongo filter."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.country import (  # noqa: E402
    COUNTRY_LABELS,
    FILTER_GROUPS,
    country_filter_query,
    job_country,
    normalize_country,
)


def _matches(doc: dict, query: dict) -> bool:
    """Tiny evaluator for the Mongo subset country_filter_query emits."""
    for key, cond in query.items():
        if key == "$and":
            if not all(_matches(doc, q) for q in cond):
                return False
        elif key == "$or":
            if not any(_matches(doc, q) for q in cond):
                return False
        else:
            value = doc.get(key)
            if isinstance(cond, dict):
                if "$regex" in cond:
                    flags = re.IGNORECASE if "i" in cond.get("$options", "") else 0
                    if value is None or not re.search(cond["$regex"], str(value), flags):
                        return False
                if "$not" in cond and value is not None and cond["$not"].search(str(value)):
                    return False
                if "$nin" in cond and value in cond["$nin"]:
                    return False
                if "$in" in cond and value not in cond["$in"]:
                    return False
            elif value != cond:
                return False
    return True


DOCS = [
    {"location": "Helsinki, Finland"},
    {"location": "Helsinki"},
    {"location": "Berlin"},
    {"location": "München"},
    {"location": "Remote (Germany)"},
    {"location": "Amsterdam (Hybrid)"},
    {"location": "Remote", "country": "remote"},
    {"location": "Remote, Europe"},
    {"location": "Worldwide"},
    {"location": "Stockholm, Sweden"},
    {"location": "Berlin", "country": "finland"},  # agent override
    {"location": "London, UK"},
    {"location": "New York, NY, USA"},
    {"location": ""},
    {"location": "Berlin (hybrid) or Helsinki"},
    {"location": "Remote (CET)"},
    {"location": "Antwerp, Belgium (hybrid)"},
    {"location": "Remote (UK-based), United Kingdom", "country": "uk"},
    {"location": "Helsinki", "country": "remote"},
]


class CountryTests(unittest.TestCase):
    def test_location_guess(self):
        self.assertEqual(job_country("Helsinki, Finland"), "finland")
        self.assertEqual(job_country("Espoo"), "finland")
        self.assertEqual(job_country("Remote, Europe"), "remote")
        self.assertEqual(job_country("Worldwide (Remote)"), "remote")
        self.assertEqual(job_country("Berlin (Remote)"), "germany")
        self.assertEqual(job_country("Zürich"), "switzerland")
        self.assertEqual(job_country("London, UK"), "uk")
        self.assertEqual(job_country("New York, NY, USA"), "other")
        self.assertEqual(job_country("Berlin (hybrid) or Helsinki"), "germany")
        self.assertEqual(job_country("Amsterdam or Munich"), "germany")
        self.assertEqual(job_country("Remote (CET)"), "remote")
        self.assertEqual(job_country("Remote (Europe)"), "remote")
        self.assertEqual(job_country("Helsinki, Finland (posting location: Europe; remote-first)"), "finland")

    def test_stored_field_wins(self):
        self.assertEqual(job_country("Berlin", "finland"), "finland")
        self.assertEqual(job_country("Helsinki", "bogus"), "finland")

    def test_normalize_country(self):
        self.assertEqual(normalize_country("Finland"), "finland")
        self.assertEqual(normalize_country("FI"), "finland")
        self.assertEqual(normalize_country("finland"), "finland")
        self.assertEqual(normalize_country("United Kingdom"), "uk")
        self.assertEqual(normalize_country("Remote"), "remote")
        self.assertEqual(normalize_country("Remote (EU)"), "remote")
        self.assertEqual(normalize_country("Helsinki, Finland"), "finland")
        self.assertEqual(normalize_country(""), "")
        self.assertEqual(normalize_country("Atlantis"), "")

    def test_groups_agree_with_job_country(self):
        for group, keys in FILTER_GROUPS.items():
            query = country_filter_query(group)
            for doc in DOCS:
                expected = job_country(doc.get("location"), doc.get("country")) in keys
                self.assertEqual(_matches(doc, query), expected, f"{group} vs {doc}")

    def test_europe_group_excludes_germany_remote_and_outside(self):
        self.assertNotIn("germany", FILTER_GROUPS["europe"])
        self.assertNotIn("remote", FILTER_GROUPS["europe"])
        self.assertNotIn("other", FILTER_GROUPS["europe"])
        self.assertIn("finland", FILTER_GROUPS["europe"])
        self.assertEqual(country_filter_query("all"), {})

    def test_filter_query_agrees_with_job_country(self):
        for key in COUNTRY_LABELS:
            query = country_filter_query(key)
            for doc in DOCS:
                expected = job_country(doc.get("location"), doc.get("country")) == key
                self.assertEqual(
                    _matches(doc, query), expected, f"{key} vs {doc}"
                )


if __name__ == "__main__":
    unittest.main()
