"""Agent job push (Claude daily push → Tracker): log_agent_jobs + /api/agent/jobs.

Does not touch a live MongoDB.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import core.agent_push as ap  # noqa: E402
import core.db_mongo as db_mongo  # noqa: E402
import core.manual_log as ml  # noqa: E402

with patch.object(db_mongo, "ensure_indexes"):
    import web.web_app as web_app  # noqa: E402
    import web.chrome_tasks as chrome_tasks  # noqa: E402
    import web.routes.jobs as jobs_routes  # noqa: E402
    import web.routes.log_job as log_job_routes  # noqa: E402

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
        with patch.object(ap, "lookup_known_job", return_value=None), \
                patch.object(ap, "save_link_only_urls",
                             side_effect=lambda *a, **k: next(outcomes)) as save:
            result = ap.log_agent_jobs(jobs, recommended_by="claude")
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
        oid = "a" * 24
        for collection, stage, stamp in (
            ("matched_jobs", "tracker", False),
            ("jobs", "unmatched", True),
            ("jobs", "awaiting_match", True),
            ("timeout_jobs", "timeout", False),
        ):
            with self.subTest(stage=stage):
                hit = {
                    "collection": collection,
                    "stage": stage,
                    "on_tracker": stage == "tracker",
                    "_id": oid,
                    "title": "Java Engineer",
                    "company": "Umbrella",
                    "match_score": 4.0,
                    "match_gate": "stack",
                    "job_id": "4123456789",
                }
                with patch.object(ap, "lookup_known_job", return_value=hit) as lookup, \
                        patch.object(ap, "save_link_only_urls") as save, \
                        patch.object(ap, "stamp_agent_recommendation") as stamp_fn:
                    result = ap.log_agent_jobs(
                        [{"url": URL_A, "title": "Other title", "reason": "fits"}]
                    )
                lookup.assert_called_once_with("4123456789", "linkedin", link=URL_A)
                save.assert_not_called()
                if stamp:
                    stamp_fn.assert_called_once_with(hit, "claude", "fits")
                else:
                    stamp_fn.assert_not_called()
                self.assertEqual(result["skipped"], [URL_A])
                self.assertEqual(result["existing"][0]["stage"], stage)
                self.assertEqual(result["existing"][0]["title"], "Java Engineer")
                self.assertEqual(result["below_threshold"], 1 if stage == "unmatched" else 0)
                self.assertEqual(result["already_on_tracker"], 1 if stage == "tracker" else 0)

    def test_career_page_url_is_deduped_by_hashed_id(self):
        url = "https://example.com/careers/jobs/123-backend"
        hit = {"collection": "matched_jobs", "stage": "tracker", "on_tracker": True}
        with patch.object(ap, "lookup_known_job", return_value=hit) as lookup, \
                patch.object(ap, "save_link_only_urls") as save:
            result = ap.log_agent_jobs([url])
        source = lookup.call_args.args[1]
        self.assertEqual(source, "manual")
        save.assert_not_called()
        self.assertEqual(result["skipped"], [url])

    def test_per_job_recommended_by_overrides_batch_default(self):
        jobs = [{"url": URL_A, "recommended_by": "Cursor"}, {"url": URL_B}]
        outcomes = iter([{"ok": [URL_A]}, {"ok": [URL_B]}])
        with patch.object(ap, "lookup_known_job", return_value=None), \
                patch.object(ap, "save_link_only_urls",
                             side_effect=lambda *a, **k: next(outcomes)) as save:
            ap.log_agent_jobs(jobs, recommended_by="claude")
        recs = [c.kwargs["recommended_by"] for c in save.call_args_list]
        self.assertEqual(recs, ["cursor", "claude"])

    def test_reason_is_passed_to_save(self):
        jobs = [{"url": URL_A, "reason": "  Python backend, English team  "}, {"url": URL_B}]
        outcomes = iter([{"ok": [URL_A]}, {"ok": [URL_B]}])
        with patch.object(ap, "lookup_known_job", return_value=None), \
                patch.object(ap, "save_link_only_urls",
                             side_effect=lambda *a, **k: next(outcomes)) as save:
            ap.log_agent_jobs(jobs)
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
        with patch.object(ap, "lookup_known_job", return_value=None), \
                patch.object(ap, "save_link_only_urls", return_value=None):
            result = ap.log_agent_jobs([URL_A])
        self.assertEqual(result["failed"], [URL_A])

    def test_stamp_skips_tracker_and_writes_jobs_row(self):
        oid = "d" * 24
        col = MagicMock()
        col.update_one.return_value.matched_count = 1
        tracker_hit = {"collection": "matched_jobs", "_id": oid}
        self.assertFalse(ap.stamp_agent_recommendation(tracker_hit, "cursor"))
        jobs_hit = {"collection": "jobs", "_id": oid}
        with patch.object(ap, "get_collection", return_value=col) as get_col:
            self.assertTrue(ap.stamp_agent_recommendation(jobs_hit, "Cursor", "  fits  "))
        get_col.assert_called_once_with("jobs")
        fields = col.update_one.call_args[0][1]["$set"]
        self.assertEqual(fields["agent_recommended_by"], "cursor")
        self.assertEqual(fields["agent_recommend_reason"], "fits")
        self.assertIn("agent_recommended_at", fields)

    def test_unmatched_existing_payload_has_open_path(self):
        oid = "b" * 24
        hit = {
            "collection": "jobs",
            "stage": "unmatched",
            "on_tracker": False,
            "_id": oid,
            "title": "Java Engineer",
            "company": "Umbrella",
            "match_score": "4.5",
            "match_gate": "german",
            "job_id": "4123456789",
        }
        row = ap.existing_from_hit(URL_A, "4123456789", hit)
        self.assertEqual(row["open_path"], f"/unmatched?open={oid}")
        self.assertEqual(row["match_score"], 4.5)
        self.assertFalse(row["on_tracker"])


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
                patch.object(log_job_routes, "log_agent_jobs", return_value=result) as log:
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
                patch.object(log_job_routes, "log_agent_jobs", return_value=result) as log:
            resp = self._post({"jobs": [URL_A], "recommended_by": "Cursor"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(log.call_args.kwargs["recommended_by"], "cursor")
        self.assertEqual(resp.get_json()["recommended_by"], "cursor")

    def test_saves_and_reports_counts(self):
        result = {"ok": [URL_A], "skipped": [URL_B], "failed": [], "existing": []}
        with patch.dict(os.environ, {"AGENT_API_TOKEN": TOKEN}), \
                patch.object(log_job_routes, "log_agent_jobs", return_value=result) as log:
            resp = self._post({"jobs": [{"url": URL_A}, {"url": URL_B}]})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual((body["saved"], body["skipped"], body["failed"]), (1, 1, 0))
        self.assertEqual(body["below_threshold"], 0)
        self.assertEqual(log.call_args.kwargs["recommended_by"], "claude")

    def test_reports_below_threshold_separately(self):
        existing = [{
            "url": URL_A,
            "job_id": "4123456789",
            "stage": "unmatched",
            "on_tracker": False,
            "title": "Java Engineer",
            "company": "Umbrella",
            "match_score": 4.0,
            "match_gate": "stack",
            "open_path": "/unmatched?open=" + "c" * 24,
        }]
        result = {
            "ok": [],
            "skipped": [URL_A],
            "failed": [],
            "existing": existing,
            "already_on_tracker": 0,
            "below_threshold": 1,
            "awaiting_match": 0,
            "timeout": 0,
        }
        with patch.dict(os.environ, {"AGENT_API_TOKEN": TOKEN}), \
                patch.object(log_job_routes, "log_agent_jobs", return_value=result):
            resp = self._post({"jobs": [{"url": URL_A}]})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["saved"], 0)
        self.assertEqual(body["below_threshold"], 1)
        self.assertEqual(body["already_on_tracker"], 0)
        self.assertEqual(body["below_threshold_jobs"], existing)
        self.assertIn("Unmatched", body["hint"])


class BatchDeleteApiTests(unittest.TestCase):
    def setUp(self):
        web_app.app.config["LOGIN_DISABLED"] = True
        self.client = web_app.app.test_client()

    def tearDown(self):
        web_app.app.config["LOGIN_DISABLED"] = False

    def test_deletes_each_id_and_counts_missing(self):
        ids = ["a" * 24, "b" * 24]
        hits = iter([{"deleted_jobs": 1, "title": "", "company": ""}, None])
        with patch.object(jobs_routes, "delete_tracker_job",
                          side_effect=lambda oid: next(hits)) as delete:
            resp = self.client.post("/api/jobs/batch-delete", json={"ids": ids})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(delete.call_count, 2)
        body = resp.get_json()
        self.assertEqual((body["deleted"], body["missing"], body["ids"]), (1, 1, [ids[0]]))

    def test_refused_while_chrome_batch_runs(self):
        with patch.dict(chrome_tasks._fetch_batch_state, {"running": True}), \
                patch.object(jobs_routes, "delete_tracker_job") as delete:
            resp = self.client.post("/api/jobs/batch-delete", json={"ids": ["a" * 24]})
        self.assertEqual(resp.status_code, 409)
        delete.assert_not_called()

    def test_rejects_empty_or_bad_ids(self):
        self.assertEqual(self.client.post("/api/jobs/batch-delete", json={"ids": []}).status_code, 400)
        self.assertEqual(self.client.post("/api/jobs/batch-delete", json={"ids": ["x"]}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
