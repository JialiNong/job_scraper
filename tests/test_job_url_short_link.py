"""Indeed short links (to.indeed.com) expand to a jk= URL before id resolution."""
import io
import sys
import unittest
import urllib.error
from pathlib import Path
from email.message import Message
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.job_url import expand_short_job_url, is_short_job_url, resolve_job_id


def _redirect(location):
    headers = Message()
    headers["Location"] = location
    return urllib.error.HTTPError("x", 302, "Found", headers, io.BytesIO())


def _opener(*effects):
    opener = mock.Mock()
    opener.open.side_effect = list(effects)
    return opener


class ShortLinkTests(unittest.TestCase):
    def test_is_short_job_url(self):
        assert is_short_job_url("https://to.indeed.com/aa4r7lclc6tz")
        assert is_short_job_url("to.indeed.com/aa4r7lclc6tz")
        assert not is_short_job_url("https://de.indeed.com/viewjob?jk=abc123")
        assert not is_short_job_url("https://www.linkedin.com/jobs/view/123/")


    def test_expand_follows_redirect_until_jk(self):
        target = "https://www.indeed.com/uie/clk?e=AAA&jk=4abb7731ef8ee21b&from=mcp"
        opener = _opener(_redirect(target))
        with mock.patch("urllib.request.build_opener", return_value=opener):
            url = expand_short_job_url("https://to.indeed.com/aa4r7lclc6tz")
        assert url == target
        assert resolve_job_id(url) == "4abb7731ef8ee21b"
        assert opener.open.call_count == 1  # never fetches the job page itself


    def test_expand_keeps_short_url_on_network_error(self):
        opener = _opener(urllib.error.URLError("offline"))
        with mock.patch("urllib.request.build_opener", return_value=opener):
            assert expand_short_job_url("to.indeed.com/x") == "https://to.indeed.com/x"


    def test_expand_leaves_normal_urls_alone(self):
        with mock.patch("urllib.request.build_opener") as build:
            assert (
                expand_short_job_url("de.indeed.com/viewjob?jk=abc123")
                == "https://de.indeed.com/viewjob?jk=abc123"
            )
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
