"""Shared job-list JS helpers (score ring, applicants label, channel).

Runs the Node assertions in tests/js/test_job_list_helpers.js.
Does not touch MongoDB.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "js" / "test_job_list_helpers.js"


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class JobListHelperTests(unittest.TestCase):
    def test_shared_helpers(self):
        result = subprocess.run(
            ["node", str(SCRIPT)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            result.stdout + "\n" + result.stderr,
        )
        self.assertIn("ok job_list helpers", result.stdout)


if __name__ == "__main__":
    unittest.main()
