"""Agent job push (Claude daily push → Tracker): log_agent_jobs + /api/agent/jobs.

Does not touch a live MongoDB.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import core.db_mongo as db_mongo  # noqa: E402
import core.manual_log as ml  # noqa: E402

with patch.object(db_mongo, "ensure_indexes"):
    import web.web_app as web_app  # noqa: E402

URL_A = "https://www.linkedin.com/jobs/view/4123456789/"
URL_B = "https://de.indeed.com/viewjob?jk=65ad798c3e603bb0"
TOKEN = "test-agent-token"


class LogAgentJobsTests(unittest.TestCase):
    def test_new_jobs_saved_with_own_identity_as_pending(self):
        jobs = [
            {"url": URL_A, "title": "Backend Engineer", "company": "Acme", "location": "Berlin"},
            URL_B,
            {"title": "no url"},
        ]
        outcomes = iter([{"ok": [URL_A]}, {"ok": [URL_B]}])
        with patch.object(ml, "lookup_known_job", return_value=None), \
                patch.object(ml, "save_link_only_urls",
                             side_effect=lambda *a, **k: next(outcomes)) as save:
            result = ml.log_agent_jobs(jobs, recommended_by="claude")
        self.assertEqual(save.call_count, 2)
        first = save.call_args_list[0]
        self.assertEqual(first.args[0], [URL_A])
        self.assertEqual(first.kwargs["status"], "pending")
        self.assertEqual(first.kwargs["recommended_by"], "claude")
        self.assertEqual(
            first.kwargs["identity"]["values"],
            {"title": "Backend Engineer", "company": "Acme", "location": "Berlin"},
        )
        self.assertEqual(result["ok"], [URL_A, URL_B])
        self.assertEqual(result["skipped"], [])

    def test_existing_job_id_in_any_collection_is_not_written(self):
        for collection, stage in (
            ("matched_jobs", "tracker"),
            ("jobs", "unmatched"),
            ("jobs", "awaiting_match"),
            ("timeout_jobs", "timeout"),
        ):
            with self.subTest(stage=stage):
                hit = {"collection": collection, "stage": stage}
                with patch.object(ml, "lookup_known_job", return_value=hit) as lookup, \
                        patch.object(ml, "save_link_only_urls") as save, \
                        patch.object(ml, "stamp_recommended_by") as stamp_rec, \
                        patch.object(ml, "stamp_manual_identity") as stamp_id:
                    result = ml.log_agent_jobs([{"url": URL_A, "title": "Other title"}])
                lookup.assert_called_once_with("4123456789", "linkedin")
                save.assert_not_called()
                stamp_rec.assert_not_called()
                stamp_id.assert_not_called()
                self.assertEqual(result["skipped"], [URL_A])
                self.assertEqual(
                    result["existing"],
                    [{"url": URL_A, "job_id": "4123456789", "stage": stage}],
                )

    def test_career_page_url_is_deduped_by_hashed_id(self):
        url = "https://example.com/careers/jobs/123-backend"
        hit = {"collection": "matched_jobs", "stage": "tracker"}
        with patch.object(ml, "lookup_known_job", return_value=hit) as lookup, \
                patch.object(ml, "save_link_only_urls") as save:
            result = ml.log_agent_jobs([url])
        source = lookup.call_args.args[1]
        self.assertEqual(source, "manual")
        save.assert_not_called()
        self.assertEqual(result["skipped"], [url])

    def test_per_job_recommended_by_overrides_batch_default(self):
        jobs = [{"url": URL_A, "recommended_by": "Cursor"}, {"url": URL_B}]
        outcomes = iter([{"ok": [URL_A]}, {"ok": [URL_B]}])
        with patch.object(ml, "lookup_known_job", return_value=None), \
                patch.object(ml, "save_link_only_urls",
                             side_effect=lambda *a, **k: next(outcomes)) as save:
            ml.log_agent_jobs(jobs, recommended_by="claude")
        recs = [c.kwargs["recommended_by"] for c in save.call_args_list]
        self.assertEqual(recs, ["cursor", "claude"])

    def test_reason_is_passed_to_save(self):
        jobs = [{"url": URL_A, "reason": "  Python backend, English team  "}, {"url": URL_B}]
        outcomes = iter([{"ok": [URL_A]}, {"ok": [URL_B]}])
        with patch.object(ml, "lookup_known_job", return_value=None), \
                patch.object(ml, "save_link_only_urls",
                             side_effect=lambda *a, **k: next(outcomes)) as save:
            ml.log_agent_jobs(jobs)
        reasons = [c.kwargs["recommend_reason"] for c in save.call_args_list]
        self.assertEqual(reasons, ["  Python backend, English team  ", None])

    def test_link_only_stub_stores_trimmed_capped_reason(self):
        saved = {}

        def fake_save(job, *a, **k):
            saved.update(job)
            return True

        long_reason = "  " + "x" * (ml.RECOMMEND_REASON_MAX_LEN + 50)
        with patch.object(ml, "is_job_on_tracker", return_value=False), \
                patch.object(ml, "save_job_record", side_effect=fake_save):
            ml.save_link_only_urls([URL_A], recommended_by="claude",
                                   recommend_reason=long_reason)
        self.assertEqual(saved["recommend_reason"], "x" * ml.RECOMMEND_REASON_MAX_LEN)
        self.assertIn("recommend_reason", ml.DETAILS_PRESERVE_FIELDS)

    def test_unknown_save_outcome_counts_as_failed(self):
        with patch.object(ml, "lookup_known_job", return_value=None), \
                patch.object(ml, "save_link_only_urls", return_value=None):
            result = ml.log_agent_jobs([URL_A])
        self.assertEqual(result["failed"], [URL_A])


class AgentJobsApiTests(unittest.TestCase):
    def setUp(self):
        self.client = web_app.app.test_client()

    def _post(self, body, token=TOKEN):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.post("/api/agent/jobs", json=body, headers=headers)

    def test_disabled_without_env_token(self):
        with patch.dict(os.environ, {"AGENT_API_TOKEN": ""}):
            self.assertEqual(self._post({"jobs": [URL_A]}).status_code, 503)

    def test_bare_job_list_body_is_accepted(self):
        body = [{"url": URL_A, "recommended_by": "gpt", "reason": "fits"}]
        result = {"ok": [URL_A], "skipped": [], "failed": [], "existing": []}
        with patch.dict(os.environ, {"AGENT_API_TOKEN": TOKEN}), \
                patch.object(web_app, "log_agent_jobs", return_value=result) as log:
            resp = self._post(body)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(log.call_args.args[0], body)

    def test_non_object_body_is_400_not_500(self):
        with patch.dict(os.environ, {"AGENT_API_TOKEN": TOKEN}):
            for body in ("just a string", 42):
                with self.subTest(body=body):
                    self.assertEqual(self._post(body).status_code, 400)

    def test_rejects_missing_or_wrong_token(self):
        with patch.dict(os.environ, {"AGENT_API_TOKEN": TOKEN}):
            self.assertEqual(self._post({"jobs": [URL_A]}, token=None).status_code, 401)
            self.assertEqual(self._post({"jobs": [URL_A]}, token="nope").status_code, 401)

    def test_validates_body(self):
        with patch.dict(os.environ, {"AGENT_API_TOKEN": TOKEN}):
            self.assertEqual(self._post({"jobs": []}).status_code, 400)
            self.assertEqual(self._post({"jobs": [URL_A] * 51}).status_code, 400)
            self.assertEqual(
                self._post({"jobs": [URL_A], "recommended_by": "bard"}).status_code, 400
            )
            self.assertEqual(
                self._post({"jobs": [{"url": URL_A, "recommended_by": "bard"}]}).status_code,
                400,
            )

    def test_top_level_recommended_by_is_passed_and_echoed(self):
        result = {"ok": [URL_A], "skipped": [], "failed": [], "existing": []}
        with patch.dict(os.environ, {"AGENT_API_TOKEN": TOKEN}), \
                patch.object(web_app, "log_agent_jobs", return_value=result) as log:
            resp = self._post({"jobs": [URL_A], "recommended_by": "Cursor"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(log.call_args.kwargs["recommended_by"], "cursor")
        self.assertEqual(resp.get_json()["recommended_by"], "cursor")

    def test_saves_and_reports_counts(self):
        result = {"ok": [URL_A], "skipped": [URL_B], "failed": [], "existing": []}
        with patch.dict(os.environ, {"AGENT_API_TOKEN": TOKEN}), \
                patch.object(web_app, "log_agent_jobs", return_value=result) as log:
            resp = self._post({"jobs": [{"url": URL_A}, {"url": URL_B}]})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual((body["saved"], body["skipped"], body["failed"]), (1, 1, 0))
        self.assertEqual(log.call_args.kwargs["recommended_by"], "claude")


class BatchDeleteApiTests(unittest.TestCase):
    def setUp(self):
        web_app.app.config["LOGIN_DISABLED"] = True
        self.client = web_app.app.test_client()

    def tearDown(self):
        web_app.app.config["LOGIN_DISABLED"] = False

    def test_deletes_each_id_and_counts_missing(self):
        ids = ["a" * 24, "b" * 24]
        hits = iter([{"deleted_jobs": 1, "title": "", "company": ""}, None])
        with patch.object(web_app, "delete_tracker_job",
                          side_effect=lambda oid: next(hits)) as delete:
            resp = self.client.post("/api/jobs/batch-delete", json={"ids": ids})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(delete.call_count, 2)
        body = resp.get_json()
        self.assertEqual((body["deleted"], body["missing"], body["ids"]), (1, 1, [ids[0]]))

    def test_refused_while_chrome_batch_runs(self):
        with patch.dict(web_app._fetch_batch_state, {"running": True}), \
                patch.object(web_app, "delete_tracker_job") as delete:
            resp = self.client.post("/api/jobs/batch-delete", json={"ids": ["a" * 24]})
        self.assertEqual(resp.status_code, 409)
        delete.assert_not_called()

    def test_rejects_empty_or_bad_ids(self):
        self.assertEqual(self.client.post("/api/jobs/batch-delete", json={"ids": []}).status_code, 400)
        self.assertEqual(self.client.post("/api/jobs/batch-delete", json={"ids": ["x"]}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
