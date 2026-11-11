"""Replace posting link on a Tracker card (dead URL → live company page).

Does not touch a live MongoDB.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from bson import ObjectId

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from page_source import page_source  # noqa: E402

import core.db_mongo as db_mongo  # noqa: E402
from core.job_url import replacement_link_fields  # noqa: E402
from core.manual_log import can_fetch_details  # noqa: E402

with patch.object(db_mongo, "ensure_indexes"):
    import web.web_app  # noqa: E402,F401
    import web.routes.jobs as jobs_routes  # noqa: E402
    from web.serialize import _normalize_link, _serialize  # noqa: E402

HTML = page_source("index")
OID = "6abbb5bf2f19f44d13721caf"
ASHBY_UID = "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"
ASHBY_URL = f"https://jobs.ashbyhq.com/acme/{ASHBY_UID}"
LI_ID = "4100000001"
LI_URL = f"https://www.linkedin.com/jobs/view/{LI_ID}/"


class ReplacementLinkFieldsTests(unittest.TestCase):
    def test_company_page_becomes_manual(self):
        fields, err = replacement_link_fields(ASHBY_URL)
        self.assertIsNone(err)
        self.assertEqual(fields["source"], "manual")
        self.assertEqual(fields["link"], ASHBY_URL)
        self.assertEqual(fields["posting_key"], f"ashby:{ASHBY_UID}")
        self.assertTrue(fields["job_id"])

    def test_linkedin_keeps_board_identity(self):
        fields, err = replacement_link_fields(
            f"https://www.linkedin.com/jobs/view/{LI_ID}/?trk=foo"
        )
        self.assertIsNone(err)
        self.assertEqual(fields["source"], "linkedin")
        self.assertEqual(fields["job_id"], LI_ID)
        self.assertEqual(fields["link"], LI_URL)
        self.assertNotIn("posting_key", fields)

    def test_scheme_less_company_url(self):
        fields, err = replacement_link_fields(
            f"jobs.ashbyhq.com/acme/{ASHBY_UID}"
        )
        self.assertIsNone(err)
        self.assertEqual(fields["link"], ASHBY_URL)

    def test_empty_and_unparseable(self):
        fields, err = replacement_link_fields("  ")
        self.assertIsNone(fields)
        self.assertEqual(err, "Paste a job URL")
        fields, err = replacement_link_fields("https://www.linkedin.com/jobs/")
        self.assertIsNone(fields)
        self.assertIn("job id", err)


class NormalizeLinkTests(unittest.TestCase):
    def test_replaced_company_url_wins_over_linkedin_job_id(self):
        self.assertEqual(
            _normalize_link({
                "source": "linkedin",
                "job_id": LI_ID,
                "link": ASHBY_URL,
            }),
            ASHBY_URL,
        )

    def test_linkedin_canonical_when_link_is_linkedin(self):
        self.assertEqual(
            _normalize_link({
                "source": "linkedin",
                "job_id": LI_ID,
                "link": f"https://www.linkedin.com/jobs/view/{LI_ID}/?eBP=abc",
            }),
            LI_URL,
        )


class CanFetchAfterReplaceTests(unittest.TestCase):
    def test_replaced_link_is_fetchable_even_with_a_jd(self):
        job = {
            "_id": OID,
            "title": "Backend Engineer",
            "summary": "ok",
            "description": "A full JD",
            "ai_pending": False,
            "recommended_by": "",
            "details_fetched_at": datetime(2026, 10, 19),
            "link_replaced_at": datetime(2026, 11, 11),
        }
        self.assertTrue(can_fetch_details(job))
        serialized = _serialize(dict(job))
        self.assertTrue(serialized["can_fetch_details"])
        self.assertEqual(serialized["link_replaced_at"], "2026-11-11 00:00")

    def test_old_complete_job_without_flag_is_not_fetchable(self):
        job = {
            "_id": OID,
            "title": "Backend Engineer",
            "summary": "ok",
            "description": "A full JD",
            "ai_pending": False,
            "recommended_by": "",
            "details_fetched_at": datetime(2026, 10, 19),
        }
        self.assertFalse(can_fetch_details(job))

    def test_fetch_after_replace_hides_the_button_for_complete_jobs(self):
        job = {
            "_id": OID,
            "title": "Backend Engineer",
            "summary": "ok",
            "description": "A full JD",
            "ai_pending": False,
            "recommended_by": "",
            "details_fetched_at": datetime(2026, 11, 11, 12, 0),
            "link_replaced_at": datetime(2026, 11, 11, 11, 0),
        }
        self.assertFalse(can_fetch_details(job))


class ReplaceLinkEndpointTests(unittest.TestCase):
    def setUp(self):
        import web.web_app as web_app
        self.web_app = web_app
        web_app.app.config["LOGIN_DISABLED"] = True
        self.client = web_app.app.test_client()

    def tearDown(self):
        self.web_app.app.config["LOGIN_DISABLED"] = False

    def test_empty_url_is_rejected(self):
        res = self.client.patch(f"/api/jobs/{OID}/link", json={"link": ""})
        self.assertEqual(res.status_code, 400)
        self.assertIn("Paste", res.get_json()["error"])

    def test_replace_updates_matched_and_jobs(self):
        matched = MagicMock()
        jobs_col = MagicMock()
        doc = {
            "_id": ObjectId(OID),
            "job_id": LI_ID,
            "source": "linkedin",
            "link": LI_URL,
            "title": "Backend Engineer",
            "posting_key": None,
        }
        matched.find_one.return_value = doc
        matched.update_one.return_value = MagicMock(matched_count=1)

        def get_col(name):
            return matched if name == "matched_jobs" else jobs_col

        with patch.object(jobs_routes, "get_collection", side_effect=get_col), patch.object(
            jobs_routes, "lookup_known_job", return_value=None
        ):
            res = self.client.patch(f"/api/jobs/{OID}/link", json={"link": ASHBY_URL})

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertFalse(data["unchanged"])
        self.assertEqual(data["link"], ASHBY_URL)
        self.assertEqual(data["source"], "manual")
        self.assertTrue(data["can_fetch_details"])
        set_fields = matched.update_one.call_args[0][1]["$set"]
        self.assertEqual(set_fields["link"], ASHBY_URL)
        self.assertEqual(set_fields["source"], "manual")
        self.assertIn("link_replaced_at", set_fields)
        self.assertEqual(set_fields["posting_key"], f"ashby:{ASHBY_UID}")
        jobs_col.update_many.assert_called_once()
        jobs_query = jobs_col.update_many.call_args[0][0]
        self.assertEqual(jobs_query, {"job_id": LI_ID, "source": "linkedin"})

    def test_duplicate_on_another_card_is_rejected(self):
        matched = MagicMock()
        matched.find_one.return_value = {
            "_id": ObjectId(OID),
            "job_id": LI_ID,
            "source": "linkedin",
            "link": LI_URL,
            "title": "Backend Engineer",
        }
        hit = {
            "_id": "bbbbbbbbbbbbbbbbbbbbbbbb",
            "on_tracker": True,
            "title": "Same role elsewhere",
        }
        with patch.object(jobs_routes, "get_collection", return_value=matched), patch.object(
            jobs_routes, "lookup_known_job", return_value=hit
        ):
            res = self.client.patch(f"/api/jobs/{OID}/link", json={"link": ASHBY_URL})
        self.assertEqual(res.status_code, 409)
        self.assertIn("Already on Tracker", res.get_json()["error"])
        matched.update_one.assert_not_called()

    def test_same_url_is_unchanged(self):
        matched = MagicMock()
        matched.find_one.return_value = {
            "_id": ObjectId(OID),
            "job_id": LI_ID,
            "source": "linkedin",
            "link": LI_URL,
            "title": "Backend Engineer",
        }
        with patch.object(jobs_routes, "get_collection", return_value=matched):
            res = self.client.patch(f"/api/jobs/{OID}/link", json={"link": LI_URL})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["unchanged"])
        matched.update_one.assert_not_called()


class ReplaceLinkUiTests(unittest.TestCase):
    def test_tracker_has_replace_row(self):
        self.assertIn("function renderReplaceLinkBlock(job)", HTML)
        self.assertIn("function saveReplaceLink(", HTML)
        self.assertIn("/api/jobs/${id}/link", HTML)
        self.assertIn("${renderReplaceLinkBlock(job)}", HTML)
        self.assertIn("Replace posting link", HTML)
        self.assertIn("Fetch job details to scrape the new page", HTML)
        self.assertIn(".replace-link-input", HTML)
        um = page_source("unmatched")
        self.assertNotIn("renderReplaceLinkBlock", um)
        self.assertNotIn("/api/jobs/${id}/link", um)


if __name__ == "__main__":
    unittest.main()
