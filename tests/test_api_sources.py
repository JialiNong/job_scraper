"""API job sources: normalisation, location gate, parsers and the shared funnel."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core import dedup  # noqa: E402
from sources import arbeitnow, arbeitsagentur, ats, base  # noqa: E402

LONG_EN_JD = (
    "We are looking for a frontend engineer to build our React and TypeScript web app. "
    "You will work closely with product and design, ship features every week, and own "
    "the quality of the user experience end to end. "
) * 6


class DedupTests(unittest.TestCase):
    def test_gender_tags_and_spelling_variants_collapse(self):
        variants = [
            "Senior Frontend Engineer (m/w/d)",
            "Senior Frontend Engineer (all genders)",
            "Senior Front-End Engineer - w/m/d",
        ]
        self.assertEqual({dedup.normalize_title(v) for v in variants}, {"senior frontend engineer"})
        self.assertEqual(dedup.normalize_title("Full-Stack Engineer (f/m/x)"), "fullstack engineer")

    def test_company_legal_suffixes_dropped(self):
        self.assertEqual(dedup.normalize_company("Zalando SE"), "zalando")
        self.assertEqual(dedup.normalize_company("Riverty (Bertelsmann)"), "riverty")
        self.assertEqual(dedup.normalize_company("Buying Labs GmbH"), "buying labs")


class StrictLocationTests(unittest.TestCase):
    def test_germany_and_unscoped_remote_pass(self):
        for loc in ("Munich, Germany", "Berlin Office · Bosnia", "Remote", "Remote · Europe",
                    "Hybrid (Hamburg)", "London; Berlin", "Remote, EMEA"):
            self.assertTrue(base.is_germany_or_remote_strict(loc), loc)

    def test_foreign_and_scoped_remote_fail(self):
        for loc in ("Austin, Texas", "Remote - California", "Remote - US", "USCA",
                    "Zürich, Switzerland", "Tokyo, Japan"):
            self.assertFalse(base.is_germany_or_remote_strict(loc), loc)

    def test_policy_none_skips_gate(self):
        job = base.SourceJob("arbeitsagentur", "1", "t", "c", "Austin, Texas", "l", location_policy="none")
        self.assertTrue(base.location_ok(job))


class TitleGateTests(unittest.TestCase):
    def test_api_extras_exclude_leadership_and_grads(self):
        for t in ("VP of Engineering", "Software Engineering Director",
                  "Software Engineer - New Grad (2027 Start)", "Software Engineer Internship"):
            self.assertTrue(base.is_api_title_excluded(t), t)
        self.assertFalse(base.is_api_title_excluded("Senior Frontend Engineer"))

    def test_feed_topic_filter(self):
        self.assertTrue(base.is_on_topic_title("Frontend Softwareentwickler (m/w/d)"))
        self.assertTrue(base.is_on_topic_title("Product Engineer"))
        self.assertFalse(base.is_on_topic_title("Sales Development Representative"))


class ArbeitsagenturTests(unittest.TestCase):
    def test_published_since_rounds_up_to_supported_values(self):
        self.assertEqual(arbeitsagentur.published_since_param(1), 1)
        self.assertEqual(arbeitsagentur.published_since_param(3), 7)
        self.assertEqual(arbeitsagentur.published_since_param(10), 14)
        self.assertEqual(arbeitsagentur.published_since_param(60), 28)

    def test_listing_maps_to_source_job(self):
        job = arbeitsagentur.to_source_job({
            "referenznummer": "10001-1003950315-S",
            "stellenangebotsTitel": "Product Engineer (m/w/d)",
            "firma": "xitavis GmbH",
            "stellenlokationen": [{"adresse": {"ort": "Nürnberg", "land": "DEUTSCHLAND"}}],
            "datumErsteVeroeffentlichung": "2026-11-02",
            "homeofficemoeglich": True,
        })
        self.assertEqual(job.link, "https://www.arbeitsagentur.de/jobsuche/jobdetail/10001-1003950315-S")
        self.assertEqual(job.location, "Nürnberg (home office possible)")
        self.assertTrue(job.remote)
        self.assertIsNotNone(job.fetch_description)


class ArbeitnowTests(unittest.TestCase):
    def test_item_maps_to_source_job(self):
        job = arbeitnow.to_source_job({
            "slug": "frontend-engineer-berlin-1", "company_name": "Acme", "title": "Frontend Engineer",
            "description": "<p>Hi</p>", "remote": True, "url": "https://www.arbeitnow.com/jobs/x",
            "tags": [], "job_types": [], "location": "", "created_at": 1794308480,
        })
        self.assertEqual(job.location, "Remote")
        self.assertEqual(job.job_id, "frontend-engineer-berlin-1")
        self.assertEqual(job.location_policy, "lenient")


class AtsParserTests(unittest.TestCase):
    entry = {"company": "Acme", "provider": "greenhouse", "slug": "acme"}

    def test_greenhouse(self):
        payload = {"jobs": [{"id": 7, "title": "Frontend Engineer", "location": {"name": "Berlin, Germany"},
                             "absolute_url": "https://boards.greenhouse.io/acme/jobs/7",
                             "content": "&lt;p&gt;Build things&lt;/p&gt;", "first_published": "2026-11-01T10:00:00Z"}]}
        with patch.object(ats, "get_json", return_value=payload):
            [job] = ats.fetch_greenhouse(self.entry)
        self.assertEqual(job.job_id, "greenhouse:acme:7")
        self.assertEqual(job.description, "<p>Build things</p>")
        self.assertEqual(job.location_policy, "strict")
        self.assertEqual(job.extra["ats_provider"], "greenhouse")

    def test_personio_xml(self):
        xml = """<?xml version="1.0"?><workzag-jobs><position><id>42</id><office>Berlin</office>
        <name>Fullstack Engineer</name><createdAt>2026-11-01T10:00:00+00:00</createdAt>
        <jobDescriptions><jobDescription><name>Your role</name><value><![CDATA[<p>React</p>]]></value>
        </jobDescription></jobDescriptions></position></workzag-jobs>"""
        with patch.object(ats, "get_text", return_value=xml):
            [job] = ats.fetch_personio({"company": "Acme", "provider": "personio", "slug": "acme"})
        self.assertEqual(job.link, "https://acme.jobs.personio.de/job/42?language=en")
        self.assertIn("React", job.description)
        self.assertEqual(job.location, "Berlin")

    def test_one_failing_board_does_not_stop_the_run(self):
        good = base.SourceJob("ats", "a", "Frontend Engineer", "Acme", "Berlin", "https://x")
        entries = [{"company": "A", "provider": "greenhouse", "slug": "a"},
                   {"company": "B", "provider": "lever", "slug": "b"}]
        with patch.dict(ats.PROVIDERS, {"greenhouse": lambda e: [good],
                                        "lever": MagicMock(side_effect=RuntimeError("boom"))}):
            jobs = ats.fetch(watchlist=entries, log=lambda *_: None)
        self.assertEqual(jobs, [good])


def _fake_db_mongo(known=False):
    jobs_coll = MagicMock()
    jobs_coll.find_one.return_value = None
    return SimpleNamespace(
        get_db=lambda: SimpleNamespace(jobs=jobs_coll),
        is_job_known=MagicMock(return_value=known),
        save_job=MagicMock(return_value=True),
        increment_scraper_stat=MagicMock(),
    )


class IngestFunnelTests(unittest.TestCase):
    def _run(self, jobs, fake, **kw):
        import core

        # ingest() does `from core import db_mongo`: swap both the module entry and the attribute
        with patch.dict(sys.modules, {"core.db_mongo": fake}), \
             patch.object(core, "db_mongo", fake, create=True), \
             patch.object(base, "find_cross_source_duplicate", return_value=None), \
             patch.object(base, "is_title_relevant_by_ai", return_value=True), \
             patch.object(base, "location_ok", return_value=True), \
             patch.object(base, "is_non_english_job_detail", return_value=(False, "en", 0.0)):
            return base.ingest(jobs, log=lambda *_: None, **kw)

    def test_saves_new_job_with_fetched_description(self):
        fake = _fake_db_mongo()
        job = base.SourceJob("ats", "greenhouse:acme:1", "Frontend Engineer", "Acme", "Berlin",
                             "https://x", fetch_description=lambda: LONG_EN_JD)
        stats = self._run([job], fake)
        self.assertEqual(stats["saved"], 1)
        doc = fake.save_job.call_args[0][0]
        self.assertEqual(doc["source"], "ats")
        self.assertEqual(doc["dedup_key"], "acme|frontend engineer")
        self.assertFalse(doc["description_incomplete"])

    def test_same_role_in_two_cities_saved_once(self):
        fake = _fake_db_mongo()
        jobs = [base.SourceJob("ats", f"gh:acme:{i}", "Frontend Engineer", "Acme", city, f"https://x/{i}",
                               description=LONG_EN_JD) for i, city in enumerate(("Berlin", "Munich"))]
        stats = self._run(jobs, fake)
        self.assertEqual((stats["saved"], stats["repeat_in_run"]), (1, 1))

    def test_known_job_skipped_and_dry_run_writes_nothing(self):
        fake = _fake_db_mongo(known=True)
        job = base.SourceJob("arbeitnow", "x", "Frontend Engineer", "Acme", "Berlin", "https://x")
        self.assertEqual(self._run([job], fake)["known"], 1)

        fake = _fake_db_mongo()
        job = base.SourceJob("arbeitnow", "y", "Frontend Engineer", "Acme", "Berlin", "https://y",
                             description=LONG_EN_JD)
        stats = self._run([job], fake, dry_run=True)
        self.assertEqual(stats["would_save"], 1)
        fake.save_job.assert_not_called()
        fake.increment_scraper_stat.assert_not_called()


if __name__ == "__main__":
    unittest.main()
