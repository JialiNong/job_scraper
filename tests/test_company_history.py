"""Tracker company history, company filter, applicant position, and mobile chip fold.

Does not touch a live MongoDB.
"""
from __future__ import annotations

import re
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from bson import ObjectId

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import core.db_mongo as db_mongo  # noqa: E402

with patch.object(db_mongo, "ensure_indexes"):
    from web.list_query import _jobs_list_query_from_args, company_match_clause  # noqa: E402
    from web.serialize import (  # noqa: E402
        _serialize,
        attach_company_history,
        company_history_item,
        parse_applicant_position,
    )
    from web.status import _status_update_fields, _timeline_update_fields  # noqa: E402

from page_source import page_source  # noqa: E402

HTML = page_source("index")  # index.html + its JS/CSS


class _FakeCursor(list):
    def sort(self, key, direction):
        return _FakeCursor(sorted(self, key=lambda d: d.get(key) or datetime.min, reverse=direction < 0))


class _FakeCollection:
    """Supports find({"$or": [company regex clauses]}) only."""

    def __init__(self, docs):
        self.docs = docs
        self.queries = []

    def find(self, query, projection=None):
        self.queries.append(query)
        patterns = [re.compile(c["company"]["$regex"], re.I) for c in query["$or"]]
        return _FakeCursor(
            d for d in self.docs
            if any(p.search(d.get("company") or "") for p in patterns)
        )


class CompanyMatchTests(unittest.TestCase):
    def test_exact_case_insensitive_whitespace_tolerant(self):
        pattern = re.compile(company_match_clause("Acme  GmbH")["company"]["$regex"], re.I)
        self.assertTrue(pattern.search("acme gmbh"))
        self.assertTrue(pattern.search("  ACME   GmbH "))
        self.assertFalse(pattern.search("Acme GmbH & Co"))
        self.assertFalse(pattern.search("Big Acme GmbH"))

    def test_regex_chars_are_escaped(self):
        pattern = re.compile(company_match_clause("A.B (Labs)+")["company"]["$regex"], re.I)
        self.assertTrue(pattern.search("a.b (labs)+"))
        self.assertFalse(pattern.search("AxB (Labs)+"))

    def test_empty_company_is_no_clause(self):
        self.assertEqual(company_match_clause("  "), {})

    def test_list_query_includes_company_filter(self):
        query = _jobs_list_query_from_args({"company": "Acme", "status": "all"})
        self.assertIn(company_match_clause("Acme"), query.get("$and", [query]))


class CompanyHistoryTests(unittest.TestCase):
    def setUp(self):
        self.current = ObjectId()
        self.docs = [
            {"_id": self.current, "company": "Acme", "title": "Frontend Dev",
             "status": "pending", "matched_at": datetime(2026, 11, 1)},
            {"_id": ObjectId(), "company": "ACME ", "title": "Web Engineer",
             "status": "rejected", "matched_at": datetime(2026, 8, 1),
             "applied_at": datetime(2026, 8, 2, 10, 0),
             "application_timeline": [
                 {"step": "applied", "at": datetime(2026, 8, 2, 10, 0)},
                 {"step": "rejected", "at": datetime(2026, 8, 20, 9, 0)},
             ]},
            {"_id": ObjectId(), "company": "Acme", "title": "React Dev",
             "status": "unsuitable", "matched_at": datetime(2026, 9, 1)},
            {"_id": ObjectId(), "company": "Other", "title": "Nope",
             "status": "applied", "matched_at": datetime(2026, 9, 1)},
        ]
        self.col = _FakeCollection(self.docs)

    def test_attaches_other_jobs_same_company_newest_first(self):
        jobs = [{"_id": str(self.current), "company": "acme"}]
        out = attach_company_history(jobs, self.col)[0]
        titles = [h["title"] for h in out["company_history"]]
        self.assertEqual(titles, ["React Dev", "Web Engineer"])
        self.assertEqual(out["company_history_total"], 2)
        self.assertEqual(len(self.col.queries), 1)

    def test_history_item_outcome_and_dates(self):
        item = company_history_item(self.docs[1])
        self.assertEqual(item["progress"], "rejected")
        self.assertEqual(item["applied_at"], "2026-08-02")
        self.assertEqual(item["last_step_at"], "2026-08-20")

    def test_side_status_without_application_has_no_progress(self):
        item = company_history_item(self.docs[2])
        self.assertEqual(item["progress"], "")
        self.assertEqual(item["status"], "unsuitable")

    def test_no_company_means_empty_history_and_no_query(self):
        out = attach_company_history([{"_id": "x", "company": ""}], self.col)[0]
        self.assertEqual(out["company_history"], [])
        self.assertEqual(self.col.queries, [])


class ApplicantPositionTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_applicant_position("25"), (25, None))
        self.assertEqual(parse_applicant_position("#7"), (7, None))
        self.assertEqual(parse_applicant_position(12), (12, None))
        self.assertEqual(parse_applicant_position(""), (None, None))
        self.assertEqual(parse_applicant_position(None), (None, None))
        self.assertIsNotNone(parse_applicant_position("abc")[1])
        self.assertIsNotNone(parse_applicant_position("0")[1])
        self.assertIsNotNone(parse_applicant_position(True)[1])

    def test_serialize_defaults_for_old_records(self):
        job = _serialize({"_id": ObjectId(), "title": "x"})
        self.assertIsNone(job["applicant_position"])
        job = _serialize({"_id": ObjectId(), "applicant_position": 30})
        self.assertEqual(job["applicant_position"], 30)

    def test_reset_not_applied_clears_it(self):
        fields = _status_update_fields({"applicant_position": 9}, "pending", datetime.now())
        self.assertIsNone(fields["applicant_position"])
        fields = _timeline_update_fields({"status": "applied", "applicant_position": 9}, [], datetime.now())
        self.assertIsNone(fields["applicant_position"])

    def test_side_status_keeps_it(self):
        fields = _status_update_fields({"applicant_position": 9}, "closed_no_reply", datetime.now())
        self.assertNotIn("applicant_position", fields)


class TrackerUiTests(unittest.TestCase):
    def test_mobile_chip_fold(self):
        self.assertIn('id="statsMoreToggle"', HTML)
        self.assertIn(".stats-bar:not(.stats-expanded)", HTML)

    def test_company_filter_wired(self):
        self.assertIn("params.set('company', companyFilter)", HTML)
        self.assertIn("function filterByCompany", HTML)
        self.assertIn("function clearCompanyFilter", HTML)
        # Edit info re-renders the clickable company link
        self.assertIn("cardCompanyHtml(payload.company, payload.location)", HTML)

    def test_applicant_position_ui(self):
        self.assertIn("/applicant-position", HTML)
        self.assertIn("renderApplicantPositionBlock(job)", HTML)


if __name__ == "__main__":
    unittest.main()
