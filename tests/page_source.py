"""
Full source of a web page for text assertions in unit tests:
templates/<name>.html + static/css/<name>.css + static/js/<name>.js
(whichever exist).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def page_source(name: str) -> str:
    parts = []
    for rel in (f"templates/{name}.html", f"static/css/{name}.css", f"static/js/{name}.js"):
        path = ROOT / rel
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)
