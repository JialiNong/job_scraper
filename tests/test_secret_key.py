"""Local logins survive a web-server restart (stable Flask secret key)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import core.db_mongo as db_mongo  # noqa: E402

with patch.object(db_mongo, "ensure_indexes"):
    import web.web_app as web_app  # noqa: E402


class SecretKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, ".flask_secret_key")
        self.file_patch = patch.object(web_app, "_LOCAL_SECRET_FILE", self.path)
        self.file_patch.start()

    def tearDown(self):
        self.file_patch.stop()
        self.tmp.cleanup()

    def test_env_key_wins(self):
        with patch.dict(os.environ, {"FLASK_SECRET_KEY": "from-env"}):
            self.assertEqual(web_app._load_secret_key(), "from-env")
        self.assertFalse(os.path.exists(self.path))

    def test_local_key_is_reused_across_restarts(self):
        with patch.dict(os.environ, {"FLASK_SECRET_KEY": ""}):
            first = web_app._load_secret_key()
            second = web_app._load_secret_key()
        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first), 32)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_key_file_is_gitignored(self):
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".flask_secret_key", gitignore.splitlines())


if __name__ == "__main__":
    unittest.main()
