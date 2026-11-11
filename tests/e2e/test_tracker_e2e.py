"""
Browser E2E tests for the Job Tracker web UI.

Run:  ./scripts/run_e2e.sh      (or: python3 -m unittest discover -s tests/e2e -v)

Each test starts from the same seeded throwaway database (see harness.py),
clicks through the real pages in headless Chrome, then checks MongoDB.
Any uncaught JavaScript error on a page fails the test.
"""
from __future__ import annotations

import time
import unittest

from harness import (
    AGENT_TOKEN,
    APPLIED_ID,
    DELETE_ID,
    PASSWORD,
    PENDING_ID,
    TIMEOUT_ID,
    UNMATCHED_ID,
    USERNAME,
    Mongo,
    Server,
    find_mongod,
    launch_browser,
    seed,
)

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

UI_TIMEOUT_MS = 10_000


def wait_for(check, what: str, timeout: float = 10.0):
    """Poll until check() returns a truthy value (DB writes land after the click)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for: {what}")


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@unittest.skipIf(find_mongod() is None, "mongod is not installed")
class TrackerE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mongo = Mongo()
        cls.db = cls.mongo.db
        try:
            cls.server = Server(cls.mongo.uri)
        except Exception:
            cls.mongo.stop()
            raise
        cls.base = cls.server.base
        cls.pw = sync_playwright().start()
        cls.browser = launch_browser(cls.pw)
        # Log in once; every test reuses the session cookie
        ctx = cls.browser.new_context()
        page = ctx.new_page()
        page.goto(cls.base + "/login")
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click(".btn-login")
        page.wait_for_url(cls.base + "/")
        cls.auth_state = ctx.storage_state()
        ctx.close()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.stop()
        cls.mongo.stop()

    def setUp(self):
        seed(self.db)
        self.js_errors = []
        self.ctx = self.browser.new_context(storage_state=self.auth_state)
        self.ctx.set_default_timeout(UI_TIMEOUT_MS)
        self.page = self.ctx.new_page()
        self.page.on("pageerror", lambda err: self.js_errors.append(str(err)))

    def tearDown(self):
        self.ctx.close()
        self.assertEqual(self.js_errors, [], "Uncaught JavaScript error on the page")

    # ── helpers ──────────────────────────────────────────────────────────

    def open_card(self, oid):
        """Deep-link to a Tracker card and wait until its body is visible."""
        self.page.goto(f"{self.base}/?open={oid}")
        progress = self.page.locator(f"#progress-{oid}")
        progress.wait_for(state="visible")
        return progress

    def confirm_modal(self):
        self.page.click(".tl-modal .tl-confirm")

    def job(self, oid):
        return self.db.matched_jobs.find_one({"_id": oid})

    # ── login ────────────────────────────────────────────────────────────

    def test_login_required_and_wrong_password_rejected(self):
        ctx = self.browser.new_context()
        page = ctx.new_page()
        page.goto(self.base + "/")
        self.assertIn("/login", page.url)
        page.fill("#username", USERNAME)
        page.fill("#password", "wrong")
        page.click(".btn-login")
        expect(page.get_by_text("Invalid username or password")).to_be_visible()
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click(".btn-login")
        page.wait_for_url(self.base + "/")
        ctx.close()

    # ── Tracker ──────────────────────────────────────────────────────────

    def test_tracker_shows_only_seeded_jobs(self):
        # Also proves the server is on the throwaway DB, not your real one
        self.page.goto(self.base + "/")
        expect(self.page.locator("#jobGrid .card")).to_have_count(3)
        expect(self.page.locator(f"#card-{PENDING_ID} .row-title")).to_have_text("Backend Engineer")
        expect(self.page.locator(f"#card-{PENDING_ID} .card-row .score-ring")).to_be_visible()
        expect(self.page.locator(f"#card-{PENDING_ID} .row-sub")).to_be_visible()
        expect(self.page.locator(f"#card-{PENDING_ID} .row-applicants")).to_have_text("first 25")
        expect(self.page.locator(f"#card-{APPLIED_ID} .row-applicants")).to_have_text("#12 at apply")
        expect(self.page.locator(f"#card-{DELETE_ID} .row-applicants")).to_have_text("200+ applicants")

    def test_status_chip_filters_the_list(self):
        self.page.goto(self.base + "/")
        expect(self.page.locator("#jobGrid .card")).to_have_count(3)
        self.page.locator('.stat-chip[data-status="pending"]').click()
        expect(self.page.locator("#jobGrid .card")).to_have_count(2)
        expect(self.page.locator(f"#card-{PENDING_ID}")).to_have_count(1)
        expect(self.page.locator(f"#card-{APPLIED_ID}")).to_have_count(0)
        expect(self.page.locator('.stat-chip[data-status="pending"].active')).to_be_visible()
        self.page.locator('.stat-chip[data-status="applied"]').click()
        expect(self.page.locator("#jobGrid .card")).to_have_count(1)
        expect(self.page.locator(f"#card-{APPLIED_ID}")).to_have_count(1)

    def test_record_applied_then_invite(self):
        progress = self.open_card(PENDING_ID)
        progress.get_by_role("button", name="+ Applied").click()
        self.confirm_modal()
        wait_for(lambda: self.job(PENDING_ID)["status"] == "applied", "status = applied")
        self.assertIsNotNone(self.job(PENDING_ID)["applied_at"])

        progress = self.page.locator(f"#progress-{PENDING_ID}")
        progress.get_by_role("button", name="+ Invite").click()
        self.confirm_modal()
        wait_for(lambda: self.job(PENDING_ID)["status"] == "interview", "status = interview")
        steps = [e["step"] for e in self.job(PENDING_ID)["application_timeline"]]
        self.assertEqual(steps, ["applied", "interview_invite"])
        expect(progress.locator(".progress-node")).to_have_count(2)

    def test_undo_applied_resets_to_not_applied(self):
        progress = self.open_card(APPLIED_ID)
        progress.get_by_role("button", name="↩ Undo").click()
        wait_for(lambda: self.job(APPLIED_ID)["status"] == "pending", "status = pending")
        job = self.job(APPLIED_ID)
        self.assertEqual(job["application_timeline"], [])
        self.assertIsNone(job["applied_at"])

    def test_side_status_keeps_application_record(self):
        self.open_card(APPLIED_ID)
        self.page.select_option(f".status-side-select[data-id='{APPLIED_ID}']", "unsuitable")
        wait_for(lambda: self.job(APPLIED_ID)["status"] == "unsuitable", "status = unsuitable")
        job = self.job(APPLIED_ID)
        self.assertIsNotNone(job["applied_at"], "side status must not wipe applied_at")
        self.assertEqual([e["step"] for e in job["application_timeline"]], ["applied"])

    def test_save_notes(self):
        self.open_card(PENDING_ID)
        self.page.fill(f"textarea.notes-textarea[data-id='{PENDING_ID}']", "Recruiter call on Friday")
        self.page.click(f"button.btn-save[data-id='{PENDING_ID}']")
        wait_for(lambda: self.job(PENDING_ID)["notes"] == "Recruiter call on Friday", "notes saved")

    def test_edit_job_info(self):
        self.open_card(PENDING_ID)
        card = self.page.locator(f"#card-{PENDING_ID}")
        card.locator(".edit-info-btn").click()
        card.locator(".info-input[data-field='title']").fill("Senior Backend Engineer")
        card.locator(".info-save-btn").click()
        wait_for(lambda: self.job(PENDING_ID)["title"] == "Senior Backend Engineer", "title saved")

    def test_replace_posting_link(self):
        pending = self.job(PENDING_ID)
        self.db.jobs.insert_one({
            "job_id": pending["job_id"],
            "source": "linkedin",
            "title": pending["title"],
            "link": pending["link"],
        })
        self.open_card(PENDING_ID)
        new_url = "https://jobs.ashbyhq.com/acme/0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"
        card = self.page.locator(f"#card-{PENDING_ID}")
        card.locator(".replace-link-input").fill(new_url)
        card.locator(".replace-link-wrap .btn-save").click()
        wait_for(lambda: self.job(PENDING_ID)["source"] == "manual", "source = manual")
        job = self.job(PENDING_ID)
        self.assertEqual(job["link"], new_url)
        self.assertIsNotNone(job.get("link_replaced_at"))
        self.assertEqual(job.get("posting_key"), "ashby:0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b")
        twin = self.db.jobs.find_one({"job_id": job["job_id"], "source": "manual"})
        self.assertIsNotNone(twin)
        self.assertEqual(twin["link"], new_url)
        expect(card.locator(".btn-apply")).to_have_attribute("href", new_url)
        expect(card.locator(".btn-fetch-details")).to_be_visible()

    def test_delete_card_removes_matched_and_jobs_docs(self):
        self.open_card(DELETE_ID)
        self.page.click(f"#card-{DELETE_ID} .delete-job-btn")
        self.confirm_modal()
        expect(self.page.locator(f"#card-{DELETE_ID}")).to_have_count(0)
        wait_for(lambda: self.job(DELETE_ID) is None, "matched_jobs doc deleted")
        self.assertIsNone(self.db.jobs.find_one({"job_id": "4100000003"}))
        self.assertIsNotNone(self.job(PENDING_ID), "other cards must stay")

    # ── Unmatched / Timeouts / Log Job ───────────────────────────────────

    def test_unmatched_card_uses_shared_chrome(self):
        self.page.goto(self.base + "/unmatched")
        card = self.page.locator(f"#card-{UNMATCHED_ID}")
        expect(card.locator(".card-row .score-ring")).to_be_visible()
        expect(card.locator(".row-sub .row-company")).to_have_text("Umbrella")
        expect(card.locator(".row-applicants")).to_have_text("80 applicants")
        expect(card.locator(".card-row .channel-chip")).to_be_visible()
        expect(card.locator(".card-body")).to_be_hidden()
        card.locator(".card-row").click()
        expect(card.locator(".card-body")).to_be_visible()
        expect(card.locator(".reason-box")).to_contain_text("Java backend required")
        expect(card.locator(".card-body .score-ring.score-lg")).to_be_visible()

    def test_unmatched_search_filters_the_list(self):
        self.page.goto(self.base + "/unmatched")
        expect(self.page.locator("#jobGrid .card")).to_have_count(1)
        self.page.fill("#searchInput", "Java")
        expect(self.page.locator(f"#card-{UNMATCHED_ID}")).to_have_count(1)
        self.page.fill("#searchInput", "zzzz-no-such-job")
        expect(self.page.locator("#jobGrid .card")).to_have_count(0)
        expect(self.page.get_by_text("No Unmatched Jobs")).to_be_visible()
        self.assertEqual(
            self.db.jobs.count_documents({"job_id": "4200000001"}),
            1,
            "search must not delete the unmatched job",
        )

    def test_unmatched_can_apply_copies_to_tracker(self):
        self.page.goto(f"{self.base}/unmatched?open={UNMATCHED_ID}")
        self.page.select_option(f"select.status-select[data-id='{UNMATCHED_ID}']", "watchlist")
        copied = wait_for(
            lambda: self.db.matched_jobs.find_one({"job_id": "4200000001"}),
            "unmatched job copied to matched_jobs",
        )
        self.assertEqual(copied["status"], "pending")
        self.page.goto(self.base + "/")
        expect(self.page.locator(f"#card-{copied['_id']} .row-title")).to_have_text("Java Engineer")

    def test_timeout_add_as_not_applied(self):
        self.page.goto(self.base + "/timeouts")
        self.page.click(f".card[data-id='{TIMEOUT_ID}'] .btn-ok")
        wait_for(lambda: self.db.matched_jobs.find_one({"job_id": "4300000001"}), "timeout job on Tracker")
        wait_for(
            lambda: self.db.timeout_jobs.find_one({"_id": TIMEOUT_ID})["review_status"] == "added",
            "timeout marked added",
        )

    def test_log_job_lookup_finds_existing_tracker_card(self):
        url = "https://www.linkedin.com/jobs/view/4100000001/"
        self.page.goto(self.base + "/manual-apply")
        self.page.fill("#urlBox", url)
        expect(self.page.get_by_text("Already on Tracker").first).to_be_visible()
        expect(self.page.get_by_text("Backend Engineer")).to_be_visible()
        self.assertEqual(self.db.matched_jobs.count_documents({}), 3)

    def test_log_job_link_only(self):
        url = "https://www.linkedin.com/jobs/view/4400000001/"
        self.page.goto(self.base + "/manual-apply")
        self.page.fill("#urlBox", url)
        self.page.fill("#jobTitle", "Data Engineer")
        self.page.fill("#jobCompany", "Wayne Enterprises")
        self.page.click("#linkOnlyBtn")
        saved = wait_for(
            lambda: self.db.matched_jobs.find_one({"job_id": "4400000001"}),
            "link-only job saved",
        )
        self.assertEqual(saved["title"], "Data Engineer")
        self.assertEqual(saved["company"], "Wayne Enterprises")
        self.assertEqual(saved["status"], "pending")

    def test_agent_api_requires_token_and_saves(self):
        url = "https://www.linkedin.com/jobs/view/4500000001/"
        api = self.ctx.request
        bad = api.post(self.base + "/api/agent/jobs", data={"jobs": [url]},
                       headers={"Authorization": "Bearer nope"})
        self.assertEqual(bad.status, 401)
        ok = api.post(self.base + "/api/agent/jobs",
                      data={"jobs": [{"url": url, "title": "ML Engineer", "reason": "fits"}]},
                      headers={"Authorization": f"Bearer {AGENT_TOKEN}"})
        self.assertEqual(ok.status, 200, ok.text())
        self.assertEqual(ok.json()["saved"], 1)
        self.assertIsNotNone(self.db.matched_jobs.find_one({"job_id": "4500000001"}))

    # ── every page loads without JS errors ───────────────────────────────

    def assert_page_styled(self, page, path):
        """The page's external stylesheet (static/css/*.css) loaded and has rules."""
        rules = page.evaluate("""() => [...document.styleSheets]
            .filter(s => (s.href || '').includes('/static/css/'))
            .reduce((n, s) => n + s.cssRules.length, 0)""")
        self.assertGreater(rules, 0, f"{path}: static stylesheet missing or empty")

    def test_all_pages_load_styled_without_js_errors(self):
        for path, marker in (
            ("/", "#jobGrid .card"),
            ("/unmatched", f"#card-{UNMATCHED_ID}"),
            ("/timeouts", f".card[data-id='{TIMEOUT_ID}']"),
            ("/manual-apply", "#urlBox"),
        ):
            with self.subTest(path=path):
                self.page.goto(self.base + path)
                self.page.locator(marker).first.wait_for(state="attached")
                self.page.wait_for_load_state("networkidle")
                self.assert_page_styled(self.page, path)

    def test_login_page_styled_before_login(self):
        ctx = self.browser.new_context()  # no session cookie
        page = ctx.new_page()
        page.goto(self.base + "/login")
        page.wait_for_load_state("networkidle")
        self.assert_page_styled(page, "/login")
        ctx.close()


if __name__ == "__main__":
    unittest.main()
