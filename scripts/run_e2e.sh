#!/usr/bin/env bash
# Browser E2E tests for the Tracker web UI.
# Starts a throwaway MongoDB + the web server + headless Chrome; never touches
# your real database. See tests/e2e/harness.py.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-/opt/miniconda3/bin/python3}"
exec "$PYTHON" -m unittest discover -s tests/e2e "$@"
