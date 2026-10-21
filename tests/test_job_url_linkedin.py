"""LinkedIn job ids from every URL shape users and agents paste."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.job_url import canonical_job_link, job_stub_from_url, resolve_job_id  # noqa: E402

AGENT_SLUG = (
    "https://www.linkedin.com/jobs/view/"
    "frontend-engineer-all-genders-at-avelios-medical-4361502751/"
)
SLUG_WITH_QUERY = (
    "https://www.linkedin.com/jobs/view/"
    "senior-software-engineer-operations-foundations-at-wolt-4480464048"
    "?originalSubdomain=de"
)
SEARCH = (
    "https://www.linkedin.com/jobs/search-results/?currentJobId=4361502751"
    "&trk=d_flagship3_company&originToLandingJobPostings=4361502751%2C4361502754"
    "&geoId=101282230&f_C=34633628"
)


class LinkedInJobIdTests(unittest.TestCase):
    def test_agent_slug_url(self):
        self.assertEqual(resolve_job_id(AGENT_SLUG), "4361502751")

    def test_slug_url_with_query_and_no_trailing_slash(self):
        self.assertEqual(resolve_job_id(SLUG_WITH_QUERY), "4480464048")

    def test_slug_and_search_urls_map_to_same_card(self):
        self.assertEqual(resolve_job_id(AGENT_SLUG), resolve_job_id(SEARCH))
        self.assertEqual(
            canonical_job_link(AGENT_SLUG),
            "https://www.linkedin.com/jobs/view/4361502751/",
        )

    def test_plain_view_url_still_works(self):
        self.assertEqual(
            resolve_job_id("https://www.linkedin.com/jobs/view/4361502751/"),
            "4361502751",
        )

    def test_slug_without_scheme(self):
        self.assertEqual(
            resolve_job_id("de.linkedin.com/jobs/view/react-dev-at-acme-123456789"),
            "123456789",
        )

    def test_slug_without_numeric_id_is_unresolved(self):
        self.assertIsNone(resolve_job_id("https://www.linkedin.com/jobs/view/foo-bar/"))

    def test_link_only_stub_from_slug(self):
        stub = job_stub_from_url(SLUG_WITH_QUERY)
        self.assertEqual(stub["job_id"], "4480464048")
        self.assertEqual(stub["link"], "https://www.linkedin.com/jobs/view/4480464048/")

    def test_scraper_helper_uses_same_rules(self):
        from scrapers.linkedin_scraper import extract_job_id_from_url
        self.assertEqual(extract_job_id_from_url(AGENT_SLUG), "4361502751")
        self.assertEqual(extract_job_id_from_url(SEARCH), "4361502751")


if __name__ == "__main__":
    unittest.main()
