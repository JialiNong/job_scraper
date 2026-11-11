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

PAGE_SCRIPTS = {
    "index": (
        "job_list.js",
        "overview.js",
        "cards.js",
        "batch.js",
        "timeline.js",
        "card-actions.js",
        "index.js",
    ),
    "unmatched": ("job_list.js", "unmatched.js"),
}


def _function_defs(text: str) -> set[str]:
    return set(re.findall(r"^(?:async )?function (\w+)", text, re.M))


class SharedCardChromeTests(unittest.TestCase):
    def test_both_pages_load_job_list_assets(self):
        index_html = (ROOT / "templates" / "index.html").read_text()
        unmatched_html = (ROOT / "templates" / "unmatched.html").read_text()
        for html in (index_html, unmatched_html):
            self.assertIn("static_url('css/job_list.css')", html)
            self.assertIn("static_url('js/job_list.js')", html)

    def test_page_source_includes_shared_helpers_once(self):
        shared = (ROOT / "static" / "js" / "job_list.js").read_text()
        for name in SHARED_FUNCS:
            self.assertIn(name, shared)
            index = page_source("index")
            unmatched = page_source("unmatched")
            self.assertEqual(index.count(name), 1, f"index duplicates {name}")
            self.assertEqual(unmatched.count(name), 1, f"unmatched duplicates {name}")

    def test_page_scripts_do_not_redefine_shared_helpers(self):
        shared_names = _function_defs((ROOT / "static" / "js" / "job_list.js").read_text())
        for page, files in PAGE_SCRIPTS.items():
            for fname in files:
                if fname == "job_list.js":
                    continue
                defs = _function_defs((ROOT / "static" / "js" / fname).read_text())
                overlap = shared_names & defs
                self.assertFalse(
                    overlap,
                    f"{page} {fname} redefines shared helpers: {sorted(overlap)}",
                )

    def test_both_rendercards_use_the_same_row_pieces(self):
        cards = (ROOT / "static" / "js" / "cards.js").read_text()
        unmatched = (ROOT / "static" / "js" / "unmatched.js").read_text()
        for src in (cards, unmatched):
            self.assertIn("scoreRingHtml(", src)
            self.assertIn('class="row-title"', src)
            self.assertIn('class="row-sub"', src)
            self.assertIn("rowApplicantsHtml(job)", src)
        self.assertIn("channelChipHtml(job)", unmatched)
        self.assertIn("rowChannelHtml(job)", cards)

    def test_shared_css_defines_the_card_pieces(self):
        css = (ROOT / "static" / "css" / "job_list.css").read_text()
        for sel in (
            ".score-ring {",
            ".row-sub {",
            ".channel-chip {",
            ".breakdown {",
            ".status-pill {",
            ".job-grid.layout-cards .card:not(.expanded) .card-body { display: none; }",
        ):
            self.assertIn(sel, css)

    def test_page_css_does_not_redefine_shared_ring(self):
        ring = re.compile(r"^\.score-ring \{", re.M)
        shared = (ROOT / "static" / "css" / "job_list.css").read_text()
        self.assertEqual(len(ring.findall(shared)), 1)
        for name in ("cards.css", "unmatched.css", "index.css"):
            text = (ROOT / "static" / "css" / name).read_text()
            self.assertIsNone(ring.search(text), f"{name} redefines .score-ring")


if __name__ == "__main__":
    unittest.main()
