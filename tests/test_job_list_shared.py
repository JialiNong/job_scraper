"""Tracker and Unmatched share job_list.js / job_list.css card chrome.

Does not touch a live MongoDB. Page source follows static_url() in the
templates, so a missing <script> / <link> fails these assertions.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from page_source import page_source

ROOT = Path(__file__).resolve().parents[1]

SHARED_FUNCS = (
    "function escHtml",
    "function scoreRingHtml",
    "function formatApplicantsLabel",
    "function channelChipHtml",
    "function postedPillHtml",
    "function toggleCardExpand",
    "function setLayout",
    "function showToast",
    "function renderBreakdown",
    "function currentDateRange",
)

JS = ROOT / "static" / "js"
CSS = ROOT / "static" / "css"
SHARED_JS = JS / "shared" / "job_list.js"
SHARED_CSS = CSS / "shared" / "job_list.css"

PAGE_SCRIPTS = {
    "index": (
        "shared/job_list.js",
        "tracker/overview.js",
        "tracker/flow.js",
        "tracker/cards.js",
        "tracker/batch.js",
        "tracker/timeline.js",
        "tracker/card-actions.js",
        "tracker/index.js",
    ),
    "unmatched": ("shared/job_list.js", "unmatched.js"),
}


def _function_defs(text: str) -> set[str]:
    return set(re.findall(r"^(?:async )?function (\w+)", text, re.M))


class SharedCardChromeTests(unittest.TestCase):
    def test_assets_live_in_shared_and_page_folders(self):
        self.assertTrue(SHARED_JS.is_file())
        self.assertTrue(SHARED_CSS.is_file())
        self.assertTrue((JS / "tracker" / "index.js").is_file())
        self.assertTrue((CSS / "tracker" / "index.css").is_file())
        self.assertFalse((JS / "index.js").exists())
        self.assertFalse((JS / "job_list.js").exists())
        self.assertFalse((CSS / "job_list.css").exists())
        self.assertTrue((JS / "unmatched.js").is_file())

    def test_both_pages_load_job_list_assets(self):
        index_html = (ROOT / "templates" / "index.html").read_text()
        unmatched_html = (ROOT / "templates" / "unmatched.html").read_text()
        for html in (index_html, unmatched_html):
            self.assertIn("static_url('css/shared/job_list.css')", html)
            self.assertIn("static_url('js/shared/job_list.js')", html)

    def test_page_source_includes_shared_helpers_once(self):
        shared = SHARED_JS.read_text()
        for name in SHARED_FUNCS:
            self.assertIn(name, shared)
            index = page_source("index")
            unmatched = page_source("unmatched")
            self.assertEqual(index.count(name), 1, f"index duplicates {name}")
            self.assertEqual(unmatched.count(name), 1, f"unmatched duplicates {name}")

    def test_page_scripts_do_not_redefine_shared_helpers(self):
        shared_names = _function_defs(SHARED_JS.read_text())
        for page, files in PAGE_SCRIPTS.items():
            for fname in files:
                if fname.endswith("job_list.js"):
                    continue
                defs = _function_defs((JS / fname).read_text())
                overlap = shared_names & defs
                self.assertFalse(
                    overlap,
                    f"{page} {fname} redefines shared helpers: {sorted(overlap)}",
                )

    def test_both_rendercards_use_the_same_row_pieces(self):
        cards = (JS / "tracker" / "cards.js").read_text()
        unmatched = (JS / "unmatched.js").read_text()
        for src in (cards, unmatched):
            self.assertIn("scoreRingHtml(", src)
            self.assertIn('class="row-title"', src)
            self.assertIn('class="row-sub"', src)
            self.assertIn("rowApplicantsHtml(job)", src)
        self.assertIn("channelChipHtml(job)", unmatched)
        self.assertIn("rowChannelHtml(job)", cards)

    def test_shared_css_defines_the_card_pieces(self):
        css = SHARED_CSS.read_text()
        for sel in (
            ".score-ring {",
            ".row-sub {",
            ".channel-chip {",
            ".breakdown {",
            ".status-pill {",
            ".job-grid.layout-cards .card:not(.expanded) .card-body { display: none; }",
        ):
            self.assertIn(sel, css)

    def test_unmatched_has_agent_recommended_chip(self):
        html = page_source("unmatched")
        self.assertIn('id="agentChip"', html)
        self.assertIn("toggleAgentFilter", html)
        self.assertIn("agent_recommended", html)
        self.assertIn("agentAlsoHtml", html)

    def test_overview_has_agent_unmatched_day_link(self):
        html = page_source("index")
        self.assertIn('id="calAgentUnmatchedLink"', html)
        self.assertIn("unmatchedAgentDayUrl", html)
        self.assertIn("agent_unmatched", html)

    def test_overview_has_flow_boards(self):
        html = page_source("index")
        self.assertIn('id="flowSection"', html)
        self.assertIn('id="weeklySection"', html)
        self.assertIn("function loadFlowStats", html)
        self.assertIn("static_url('js/tracker/flow.js')", (ROOT / "templates" / "index.html").read_text())
        self.assertIn("static_url('css/tracker/flow.css')", (ROOT / "templates" / "index.html").read_text())

    def test_page_css_does_not_redefine_shared_ring(self):
        ring = re.compile(r"^\.score-ring \{", re.M)
        shared = SHARED_CSS.read_text()
        self.assertEqual(len(ring.findall(shared)), 1)
        for rel in ("tracker/cards.css", "unmatched.css", "tracker/index.css"):
            text = (CSS / rel).read_text()
            self.assertIsNone(ring.search(text), f"{rel} redefines .score-ring")


if __name__ == "__main__":
    unittest.main()
