"""
Full source of the Tracker page for text assertions in unit tests:
templates/index.html + static/css/index.css + static/js/index.js.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TRACKER_PAGE = "\n".join(
    (ROOT / rel).read_text(encoding="utf-8")
    for rel in ("templates/index.html", "static/css/index.css", "static/js/index.js")
)
