"""
Full source of a web page for text assertions in unit tests:
the template plus every CSS/JS file it loads through static_url().
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_STATIC_URL = re.compile(r"static_url\('((?:css|js)/[^']+)'\)")


def page_source(name: str) -> str:
    parts = []
    seen = set()
    html_path = ROOT / "templates" / f"{name}.html"
    html = ""
    if html_path.exists():
        html = html_path.read_text(encoding="utf-8")
        parts.append(html)
        seen.add(html_path)
    for rel in _STATIC_URL.findall(html):
        path = ROOT / "static" / rel
        if path.exists() and path not in seen:
            seen.add(path)
            parts.append(path.read_text(encoding="utf-8"))
    # Fallback when a page has no static_url() yet
    for rel in (f"static/css/{name}.css", f"static/js/{name}.js"):
        path = ROOT / rel
        if path.exists() and path not in seen:
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)
