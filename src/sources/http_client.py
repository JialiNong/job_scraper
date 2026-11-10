"""
Tiny stdlib HTTP client for the API job sources (no Playwright, no requests).

Keeps the cloud runner's dependencies small: GitHub Actions only needs
pymongo / openai / lingua for the API pipeline.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "job-scraper/1.0 (personal job search; API client)"
DEFAULT_TIMEOUT = 25


class HttpError(Exception):
    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} for {url}")
        self.status = status
        self.url = url
        self.body = body


def build_url(base: str, params: dict | None = None) -> str:
    if not params:
        return base
    clean = {k: v for k, v in params.items() if v is not None and v != ""}
    sep = "&" if "?" in base else "?"
    return base + sep + urllib.parse.urlencode(clean, doseq=True)


def get_text(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = 2,
) -> str:
    """GET a URL and return the body. Retries on 429 / 5xx / network errors."""
    full = build_url(url, params)
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json, */*"}
    req_headers.update(headers or {})
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(full, headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            last_exc = HttpError(exc.code, full, body)
            if exc.code not in (429, 500, 502, 503, 504):
                raise last_exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_exc = exc
        if attempt < retries:
            time.sleep(2 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


def get_json(url: str, params: dict | None = None, headers: dict | None = None, **kw):
    return json.loads(get_text(url, params=params, headers=headers, **kw))
