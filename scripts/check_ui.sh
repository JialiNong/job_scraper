#!/usr/bin/env bash
# UI chrome check (Filters sheet, select-closes / custom-date in panel, details mask, phone selects).
# Layout / overlay contracts only — does not assert MongoDB.
# Throwaway mongod + server; never touches the real Tracker database.
# Run this after editing templates/, static/css/, or static/js/.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-/opt/miniconda3/bin/python3}"
exec "$PYTHON" tests/ui/check_tracker_chrome.py "$@"
