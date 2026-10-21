"""Reapplied timeline step: apply again to the same job (e.g. a repost).

Does not touch a live MongoDB.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import core.db_mongo as db_mongo  # noqa: E402

with patch.object(db_mongo, "ensure_indexes"):
    from web.web_app import (  # noqa: E402
        JOURNEY_TRACK_STEPS,
        TIMELINE_STEPS,
        _normalize_timeline,
        _status_from_timeline,
        _timeline_update_fields,
        application_progress,
        jobs_status_filter_query,
    )

HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

FIRST = datetime(2026, 10, 3, 10, 0)
REJECT = datetime(2026, 10, 10, 9, 0)
AGAIN = datetime(2026, 10, 21, 18, 0)
ROUND1 = datetime(2026, 10, 28, 11, 0)


def _timeline(*steps):
    return [{"step": s, "at": at} for s, at in steps]


class ReappliedStatusTests(unittest.TestCase):
    def test_step_is_known(self):
        self.assertEqual(TIMELINE_STEPS["reapplied"], "Reapplied")
        self.assertIn("reapplied", JOURNEY_TRACK_STEPS)
        raw = [{"step": "reapplied", "at": "2026-10-21 18:00"}]
        self.assertEqual(_normalize_timeline(raw)[0]["step"], "reapplied")

    def test_reapplied_after_rejected_is_applied(self):
        tl = _timeline(("applied", FIRST), ("rejected", REJECT), ("reapplied", AGAIN))
        self.assertEqual(_status_from_timeline(tl), "applied")
        job = {"status": "applied", "applied_at": FIRST, "application_timeline": tl}
        self.assertEqual(application_progress(job), "applied")

    def test_rounds_after_reapplied_are_interview(self):
        tl = _timeline(
            ("applied", FIRST), ("reapplied", AGAIN),
            ("interview_invite", AGAIN), ("interview_1", ROUND1),
        )
        self.assertEqual(_status_from_timeline(tl), "interview")

    def test_rejected_after_reapplied(self):
        tl = _timeline(("applied", FIRST), ("reapplied", AGAIN), ("rejected", ROUND1))
        self.assertEqual(_status_from_timeline(tl), "rejected")

    def test_applied_at_keeps_first_apply(self):
        job = {
            "status": "rejected",
            "applied_at": FIRST,
            "application_timeline": _timeline(("applied", FIRST), ("rejected", REJECT)),
        }
        tl = _timeline(("applied", FIRST), ("rejected", REJECT), ("reapplied", AGAIN))
        fields = _timeline_update_fields(job, tl, datetime(2026, 10, 21, 18, 5))
        self.assertEqual(fields["status"], "applied")
        self.assertEqual(fields["applied_at"], FIRST)

    def test_side_status_repost_keeps_overlay(self):
        job = {
            "status": "repost",
            "applied_at": FIRST,
            "application_timeline": _timeline(("applied", FIRST)),
        }
        tl = _timeline(("applied", FIRST), ("reapplied", AGAIN))
        fields = _timeline_update_fields(job, tl, AGAIN)
        self.assertEqual(fields["status"], "repost")
        job["application_timeline"] = tl
        self.assertEqual(application_progress(job), "applied")

    def test_applied_filter_matches_reapplied_last_step(self):
        query = jobs_status_filter_query("applied")
        self.assertIn("reapplied", str(query))


class TimelineEndpointTests(unittest.TestCase):
    def setUp(self):
        import web.web_app as web_app
        self.web_app = web_app
        web_app.app.config["LOGIN_DISABLED"] = True
        self.client = web_app.app.test_client()

    def tearDown(self):
        self.web_app.app.config["LOGIN_DISABLED"] = False

    def test_unknown_step_is_rejected_not_dropped(self):
        col = MagicMock()
        with patch.object(self.web_app, "get_collection", return_value=col):
            res = self.client.patch(
                "/api/jobs/6abbb5bf2f19f44d13721caf/timeline",
                json={"timeline": [
                    {"step": "applied", "at": "2026-09-29 19:44"},
                    {"step": "bogus", "at": "2026-10-21 18:00"},
                ]},
            )
        self.assertEqual(res.status_code, 400)
        self.assertIn("bogus", res.get_json()["error"])
        col.update_one.assert_not_called()

    def test_reapplied_is_saved(self):
        job = {
            "_id": "x", "status": "rejected", "applied_at": FIRST,
            "application_timeline": _timeline(("applied", FIRST), ("rejected", REJECT)),
        }
        col = MagicMock()
        col.find_one.return_value = job
        with patch.object(self.web_app, "get_collection", return_value=col):
            res = self.client.patch(
                "/api/jobs/6abbb5bf2f19f44d13721caf/timeline",
                json={"timeline": [
                    {"step": "applied", "at": "2026-09-29 19:44"},
                    {"step": "rejected", "at": "2026-10-05 11:09"},
                    {"step": "reapplied", "at": "2026-10-21 18:00"},
                ]},
            )
        data = res.get_json()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(data["status"], "applied")
        self.assertEqual(data["application_timeline"][-1]["step"], "reapplied")


class ReappliedUiTests(unittest.TestCase):
    def test_card_pill_shows_reapplied(self):
        self.assertIn("function cardStatusView(status, timeline)", HTML)
        self.assertIn("return { cls: 'reapplied', label: 'Reapplied' };", HTML)
        self.assertIn("${statusPillHtml(status, job.application_timeline)}", HTML)
        self.assertIn(".status-pill.s-reapplied", HTML)

    def test_save_reports_dropped_steps(self):
        self.assertIn("if (saved.length < timeline.length) {", HTML)

    def test_label_and_actions(self):
        self.assertIn("reapplied:        'Reapplied'", HTML)
        self.assertIn("if (last === 'rejected') return [reapply];", HTML)
        self.assertIn("if (last === 'applied' || last === 'reapplied') {", HTML)
        self.assertIn("if (last === 'applied' || last === 'reapplied') return 'applied';", HTML)
        self.assertIn(".progress-node.step-reapplied", HTML)


if __name__ == "__main__":
    unittest.main()
