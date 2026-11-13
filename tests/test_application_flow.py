"""Overview Sankey sinks and weekly-by-platform aggregation.

Does not touch a live MongoDB.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from web.flow import (  # noqa: E402
    build_sankey,
    build_weekly_by_platform,
    classify_flow_sink,
    job_flow_links,
    platform_bucket,
    week_monday,
)

APPLIED = datetime(2026, 9, 14, 10, 0)  # Monday
INVITE = datetime(2026, 9, 21, 11, 0)
ROUND1 = datetime(2026, 9, 28, 11, 0)
ROUND2 = datetime(2026, 10, 5, 11, 0)
REJECT = datetime(2026, 10, 12, 9, 0)
TODAY = date(2026, 11, 13)


def _job(status="applied", source="linkedin", steps=None, applied_at=APPLIED, **extra):
    timeline = []
    for step, at in steps or (("applied", applied_at),):
        timeline.append({"step": step, "at": at})
    job = {
        "status": status,
        "source": source,
        "applied_at": applied_at,
        "application_timeline": timeline,
    }
    job.update(extra)
    return job


class PlatformAndWeekTests(unittest.TestCase):
    def test_platform_bucket(self):
        self.assertEqual(platform_bucket("linkedin"), "linkedin")
        self.assertEqual(platform_bucket("Indeed"), "indeed")
        self.assertEqual(platform_bucket("ats"), "other")
        self.assertEqual(platform_bucket("manual"), "other")
        self.assertEqual(platform_bucket(""), "other")
        self.assertEqual(platform_bucket(None), "other")

    def test_week_monday_is_iso(self):
        self.assertEqual(week_monday(datetime(2026, 11, 13, 8, 0)), date(2026, 11, 9))
        self.assertEqual(week_monday("2026-09-14 10:00"), date(2026, 9, 14))
        self.assertIsNone(week_monday(None))


class ClassifyFlowSinkTests(unittest.TestCase):
    def test_waiting_after_apply(self):
        self.assertEqual(classify_flow_sink(_job()), "waiting")

    def test_rejected_without_interview(self):
        job = _job(status="rejected", steps=(
            ("applied", APPLIED),
            ("rejected", REJECT),
        ))
        self.assertEqual(classify_flow_sink(job), "rejected")
        self.assertEqual(job_flow_links(job), [("applied", "rejected")])

    def test_closed_no_reply_counts_as_waiting(self):
        job = _job(status="closed_no_reply")
        self.assertEqual(classify_flow_sink(job), "waiting")

    def test_closed_no_reply_keeps_interview_path(self):
        job = _job(status="closed_no_reply", steps=(
            ("applied", APPLIED),
            ("interview_invite", INVITE),
        ))
        self.assertEqual(classify_flow_sink(job), "awaiting")

    def test_awaiting_interview(self):
        job = _job(status="interview", steps=(
            ("applied", APPLIED),
            ("interview_invite", INVITE),
        ))
        self.assertEqual(classify_flow_sink(job), "awaiting")
        self.assertEqual(
            job_flow_links(job),
            [("applied", "interview"), ("interview", "awaiting")],
        )

    def test_rejected_at_invite(self):
        job = _job(status="rejected", steps=(
            ("applied", APPLIED),
            ("interview_invite", INVITE),
            ("rejected", REJECT),
        ))
        self.assertEqual(classify_flow_sink(job), "rejected_invite")

    def test_after_round_1_and_reject(self):
        now = datetime(2026, 11, 13, 22, 0)
        waiting = _job(status="interview", steps=(
            ("applied", APPLIED),
            ("interview_invite", INVITE),
            ("interview_1", ROUND1),
        ))
        rejected = _job(status="rejected", steps=(
            ("applied", APPLIED),
            ("interview_invite", INVITE),
            ("interview_1", ROUND1),
            ("rejected", REJECT),
        ))
        self.assertEqual(classify_flow_sink(waiting, now=now), "round_1")
        self.assertEqual(classify_flow_sink(rejected, now=now), "rejected_1")

    def test_upcoming_round_1_uses_future_timestamp(self):
        now = datetime(2026, 11, 13, 22, 0)
        future = datetime(2026, 11, 19, 14, 0)
        job = _job(status="interview", steps=(
            ("applied", APPLIED),
            ("interview_invite", INVITE),
            ("interview_1", future),
        ))
        self.assertEqual(classify_flow_sink(job, now=now), "round_1_upcoming")
        self.assertEqual(
            job_flow_links(job, now=now),
            [("applied", "interview"), ("interview", "round_1_upcoming")],
        )

    def test_aware_mongo_timestamp_does_not_crash(self):
        now = datetime(2026, 11, 13, 22, 0)
        future = datetime(2026, 11, 19, 14, 0, tzinfo=timezone.utc)
        past = datetime(2026, 11, 13, 9, 30, tzinfo=timezone.utc)
        upcoming = _job(status="interview", steps=(
            ("applied", APPLIED),
            ("interview_1", future),
        ))
        after = _job(status="interview", steps=(
            ("applied", APPLIED),
            ("interview_1", past),
        ))
        self.assertEqual(classify_flow_sink(upcoming, now=now), "round_1_upcoming")
        self.assertEqual(classify_flow_sink(after, now=now), "round_1")

    def test_round_2_and_later(self):
        r2 = _job(status="interview", steps=(
            ("applied", APPLIED),
            ("interview_2", ROUND2),
        ))
        later = _job(status="interview", steps=(
            ("applied", APPLIED),
            ("interview_final", ROUND2),
        ))
        later_rej = _job(status="rejected", steps=(
            ("applied", APPLIED),
            ("interview_3", ROUND2),
            ("rejected", REJECT),
        ))
        self.assertEqual(classify_flow_sink(r2), "round_2")
        self.assertEqual(classify_flow_sink(later), "later")
        self.assertEqual(classify_flow_sink(later_rej), "rejected_later")

    def test_offer(self):
        job = _job(status="offer", steps=(
            ("applied", APPLIED),
            ("interview_1", ROUND1),
            ("offer", ROUND2),
        ))
        self.assertEqual(classify_flow_sink(job), "offer")

    def test_flat_status_without_timeline_hydrates(self):
        rejected = {
            "status": "rejected",
            "applied_at": APPLIED,
            "updated_at": REJECT,
        }
        interview = {
            "status": "interview",
            "applied_at": APPLIED,
            "updated_at": ROUND1,
        }
        self.assertEqual(classify_flow_sink(rejected), "rejected")
        self.assertEqual(classify_flow_sink(interview), "round_1")

    def test_reapplied_restarts_the_track(self):
        job = _job(status="applied", steps=(
            ("applied", APPLIED),
            ("interview_1", ROUND1),
            ("rejected", REJECT),
            ("reapplied", datetime(2026, 10, 21, 18, 0)),
        ))
        self.assertEqual(classify_flow_sink(job), "waiting")


class BuildSankeyTests(unittest.TestCase):
    def test_counts_one_path_per_job(self):
        jobs = [
            _job(),
            _job(),
            _job(status="rejected", steps=(("applied", APPLIED), ("rejected", REJECT))),
            _job(status="interview", steps=(
                ("applied", APPLIED),
                ("interview_invite", INVITE),
                ("interview_1", ROUND1),
            )),
        ]
        data = build_sankey(jobs, now=datetime(2026, 11, 13, 22, 0))
        by_id = {n["id"]: n["value"] for n in data["nodes"]}
        links = {(l["source"], l["target"]): l["value"] for l in data["links"]}
        self.assertEqual(data["total"], 4)
        self.assertEqual(by_id["applied"], 4)
        self.assertEqual(by_id["waiting"], 2)
        self.assertEqual(by_id["rejected"], 1)
        self.assertEqual(by_id["interview"], 1)
        self.assertEqual(by_id["round_1"], 1)
        self.assertEqual(links[("applied", "waiting")], 2)
        self.assertEqual(links[("applied", "rejected")], 1)
        self.assertEqual(links[("applied", "interview")], 1)
        self.assertEqual(links[("interview", "round_1")], 1)
        self.assertNotIn("offer", by_id)

    def test_empty(self):
        data = build_sankey([])
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["links"], [])
        self.assertEqual(data["nodes"][0]["id"], "applied")


class WeeklyByPlatformTests(unittest.TestCase):
    def test_stacks_by_source_and_fills_weeks(self):
        jobs = [
            _job(source="linkedin", applied_at=datetime(2026, 9, 14, 10, 0)),
            _job(source="linkedin", applied_at=datetime(2026, 9, 16, 10, 0)),
            _job(source="indeed", applied_at=datetime(2026, 9, 15, 10, 0)),
            _job(source="ats", applied_at=datetime(2026, 9, 22, 10, 0)),
        ]
        data = build_weekly_by_platform(jobs, today=TODAY)
        self.assertEqual(data["weeks"][0]["week_start"], "2026-09-14")
        self.assertEqual(data["weeks"][0]["linkedin"], 2)
        self.assertEqual(data["weeks"][0]["indeed"], 1)
        self.assertEqual(data["weeks"][0]["other"], 0)
        self.assertEqual(data["weeks"][1]["week_start"], "2026-09-21")
        self.assertEqual(data["weeks"][1]["other"], 1)
        self.assertEqual(data["weeks"][-1]["week_start"], "2026-11-09")
        self.assertEqual(data["totals"]["linkedin"], 2)
        self.assertEqual(data["totals"]["indeed"], 1)
        self.assertEqual(data["totals"]["other"], 1)
        self.assertEqual(data["totals"]["total"], 4)

    def test_reapplied_counts_on_its_week(self):
        job = _job(source="linkedin", steps=(
            ("applied", datetime(2026, 9, 14, 10, 0)),
            ("reapplied", datetime(2026, 10, 21, 18, 0)),
        ))
        data = build_weekly_by_platform([job], today=date(2026, 10, 25))
        by_week = {w["week_start"]: w for w in data["weeks"]}
        self.assertEqual(by_week["2026-09-14"]["linkedin"], 1)
        self.assertEqual(by_week["2026-10-19"]["linkedin"], 1)
        self.assertEqual(data["totals"]["total"], 2)

    def test_empty_weekly(self):
        data = build_weekly_by_platform([], today=TODAY)
        self.assertEqual(data["weeks"], [])
        self.assertEqual(data["totals"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
