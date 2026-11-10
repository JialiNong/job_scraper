#!/usr/bin/env bash
# Claude Code Stop hook: when Claude finishes a turn, run unit + browser E2E
# tests IF the web UI's code changed since the last run.
#
#   - Watches the content of src/web, src/core, templates, tests/ (not git state,
#     so committing does not trigger a re-run).
#   - Pass: prints a one-line note and lets Claude stop.
#   - Fail: exit 2 + failure summary on stderr, so Claude sees it and fixes it.
#     The same failing code is reported once only (no endless loop).
#   - Full output of the last run: logs/e2e_last.log
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/..}" || exit 0

PYTHON="${PYTHON:-/opt/miniconda3/bin/python3}"
STATE_FILE="logs/.e2e_hook_state"
LOG_FILE="logs/e2e_last.log"
mkdir -p logs

fingerprint=$(git ls-files -co --exclude-standard -- src/web src/core templates tests \
  | grep -v '__pycache__' | sort | xargs shasum 2>/dev/null | shasum | cut -d' ' -f1)

last=""
[ -f "$STATE_FILE" ] && last=$(cut -d' ' -f1 "$STATE_FILE")
[ "$fingerprint" = "$last" ] && exit 0   # nothing relevant changed (or already reported)

# Can't run E2E without a local mongod; don't block Claude over setup.
if ! command -v mongod >/dev/null && [ ! -x /opt/homebrew/bin/mongod ]; then
  echo "{\"systemMessage\": \"E2E skipped: mongod not installed\"}"
  exit 0
fi

{
  echo "== unit tests =="; "$PYTHON" -m unittest discover -s tests 2>&1; unit=$?
  echo; echo "== e2e tests =="; "$PYTHON" -m unittest discover -s tests/e2e -v 2>&1; e2e=$?
  echo "unit_exit=$unit e2e_exit=$e2e"
} > "$LOG_FILE" 2>&1

if grep -q "unit_exit=0 e2e_exit=0" "$LOG_FILE"; then
  echo "$fingerprint pass" > "$STATE_FILE"
  ran=$(grep -E "^Ran [0-9]+ tests" "$LOG_FILE" | awk '{s+=$2} END {print s}')
  echo "{\"systemMessage\": \"Tests passed: ${ran} (unit + E2E)\"}"
  exit 0
fi

echo "$fingerprint fail" > "$STATE_FILE"
{
  echo "Unit/E2E tests failed after your changes. Fix them before finishing."
  echo "Full log: $LOG_FILE (rerun: ./scripts/run_e2e.sh)"
  echo
  # Failure blocks (FAIL:/ERROR: headers + tracebacks), capped
  awk '/^(FAIL|ERROR):/{p=1} /^Ran [0-9]+ tests/{p=0} p' "$LOG_FILE" | head -80
  grep -E "^(FAILED|OK)" "$LOG_FILE"
} >&2
exit 2
