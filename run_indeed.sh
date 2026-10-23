#!/bin/bash
# run_indeed.sh — Indeed-only scrape + AI match with a posted-within window in days.
#
# Same keyword loop as the full Indeed lane (DEFAULT_KEYWORDS,
# INDEED_JOBS_PER_PAGE). fromage is chosen per run from --days; Indeed only
# offers 1, 3, 7 or 14 days.
#
# Usage:
#   caffeinate -i ./run_indeed.sh                 # default: last 1 day
#   caffeinate -i ./run_indeed.sh --days 7        # last 7 days
#   ./run_indeed.sh --days 3 -p 2 -j 15           # explicit pages + cards
#
# Options:
#   --days N             Posted within N days: 1, 3, 7 or 14 (default: 1)
#   -p / --max-pages N   Pages per keyword (default: FULL_MAX_PAGES)
#   -j / --max-jobs N    Cards per page (default: INDEED_JOBS_PER_PAGE)
#   -h / --help

PROJECT_DIR="/Users/carrienon/Desktop/code-project/job_scraper"
LOG_DIR="$PROJECT_DIR/logs"
PYTHON="/opt/miniconda3/bin/python3"

mkdir -p "$LOG_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_DIR/main.log"
}

trap 'log "ERROR: Script failed at line $LINENO"' ERR

DAYS=1
MAX_PAGES=""
MAX_JOBS=""

usage() {
    sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --days)
            DAYS="$2"
            shift 2
            ;;
        --days=*)
            DAYS="${1#*=}"
            shift
            ;;
        -p|--max-pages)
            MAX_PAGES="$2"
            shift 2
            ;;
        -j|--max-jobs)
            MAX_JOBS="$2"
            shift 2
            ;;
        -h|--help)
            usage 0
            ;;
        *)
            # Positional fallback: ./run_indeed.sh 7
            if [[ "$1" =~ ^[0-9]+$ ]] && [ -z "${DAYS_SET:-}" ]; then
                DAYS="$1"
                DAYS_SET=1
                shift
            else
                log "Unknown argument: $1"
                usage 1
            fi
            ;;
    esac
done

case "$DAYS" in
    1|3|7|14) ;;
    *)
        log "Invalid --days value: $DAYS (Indeed only offers 1, 3, 7 or 14)"
        exit 1
        ;;
esac

release_pipeline_lock() {
    "$PYTHON" -c "
import sys
sys.path.insert(0, '$PROJECT_DIR/src')
from core.pipeline_lock import release_pipeline_lock
release_pipeline_lock(pid=int('$$'))
" 2>/dev/null || true
}

if ! "$PYTHON" -c "
import sys
sys.path.insert(0, '$PROJECT_DIR/src')
from core.pipeline_lock import acquire_pipeline_lock
ok, msg = acquire_pipeline_lock(pid=int('$$'))
print(msg)
raise SystemExit(0 if ok else 75)
"; then
    log "⚠️  Pipeline already running — not starting a second Chrome (that would kill the first scrape). Exiting."
    exit 0
fi
trap release_pipeline_lock EXIT

log "=== Indeed ${DAYS}d Scrape + Match Started ==="

CHROME_PID=""
if lsof -nP -iTCP:9222 -sTCP:LISTEN >/dev/null 2>&1; then
    log "Chrome remote debugging already listening on 9222, reusing it"
else
    log "Starting Chrome with remote debugging..."
    /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
      --remote-debugging-port=9222 \
      --user-data-dir="/tmp/chrome_selenium" \
      --no-first-run \
      --no-default-browser-check \
      > "$LOG_DIR/chrome.log" 2>&1 &
    CHROME_PID=$!
    log "Chrome started (PID: $CHROME_PID)"
fi

for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    if lsof -nP -iTCP:9222 -sTCP:LISTEN >/dev/null 2>&1; then
        log "Chrome remote debugging is ready"
        break
    fi
    sleep 0.5
done

if ! lsof -nP -iTCP:9222 -sTCP:LISTEN >/dev/null 2>&1; then
    log "⚠️  WARNING: Chrome debugging port 9222 is not open. See $LOG_DIR/chrome.log"
fi

cd "$PROJECT_DIR" || exit 1
# shellcheck source=scripts/pipeline_common.sh
source "$PROJECT_DIR/scripts/pipeline_common.sh"

if [ -z "$MAX_PAGES" ]; then
    MAX_PAGES="$($PYTHON -c "import sys; sys.path.insert(0, 'src'); from core.config import FULL_MAX_PAGES; print(FULL_MAX_PAGES)")"
fi
if [ -z "$MAX_JOBS" ]; then
    MAX_JOBS="$($PYTHON -c "import sys; sys.path.insert(0, 'src'); from core.config import INDEED_JOBS_PER_PAGE; print(INDEED_JOBS_PER_PAGE)")"
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
SCRAPE_LOG="$LOG_DIR/indeed_${DAYS}d_${STAMP}.log"
MATCH_LOG="$LOG_DIR/matcher_indeed_${DAYS}d_${STAMP}.log"

log "Indeed lane — last ${DAYS}d, ${MAX_PAGES} pages/keyword × ${MAX_JOBS} cards"
run_source_lane "Indeed (${DAYS}d)" "indeed" \
  "$SCRAPE_LOG" "$MATCH_LOG" \
  $PYTHON src/scrapers/indeed_scraper.py \
  --days "$DAYS" \
  --max-pages "$MAX_PAGES" \
  --max-jobs "$MAX_JOBS"

INDEED_EXIT="$(read_lane_exit indeed scrape)"
INDEED_MATCH_EXIT="$(read_lane_exit indeed match)"

if [ -n "$CHROME_PID" ]; then
    log "Closing Chrome..."
    kill $CHROME_PID 2>/dev/null || true
fi

log "=== Indeed ${DAYS}d Pipeline Completed ==="
log "Indeed scrape: $([ "$INDEED_EXIT" -eq 0 ] && echo '✅' || echo '❌')"
log "Indeed match:  $([ "$INDEED_MATCH_EXIT" -eq 0 ] && echo '✅' || echo '❌')"

# Count this run's work (since it started), not jobs posted in the window.
STATS=$($PYTHON << EOF
import sys, os
sys.path.insert(0, os.path.join("$PROJECT_DIR", "src"))
from core.db_mongo import get_collection
from datetime import datetime
try:
    jobs         = get_collection("jobs")
    matched_jobs = get_collection("matched_jobs")
    since = datetime.strptime("$STAMP", "%Y%m%d_%H%M%S")
    scraped = jobs.count_documents({"source": "indeed", "created_at": {"\$gte": since}})
    matched = matched_jobs.count_documents({"source": "indeed", "matched_at": {"\$gte": since}})
    print(f"{scraped} {matched}")
except Exception:
    print("0 0")
EOF
)

SCRAPED=$(echo "$STATS" | awk '{print $1}')
MATCHED=$(echo "$STATS" | awk '{print $2}')
log "This run (Indeed ${DAYS}d): ${SCRAPED} jobs scraped, ${MATCHED} high-quality matches"

log "Pushing today's matches to Telegram..."
$PYTHON -c "
import sys
sys.path.insert(0, 'src')
from core.match_digest import push_todays_matched_jobs
push_todays_matched_jobs()
" >> "$LOG_DIR/telegram_push_$(date +%Y%m%d).log" 2>&1 \
  && log "✅ Telegram match digest sent" \
  || log "⚠️  WARNING: Telegram match digest failed (see logs/telegram_push_*.log)"

osascript -e "display notification \"Indeed ${DAYS}d: ${SCRAPED} scraped, ${MATCHED} matched\" with title \"Indeed Scrape Done\" sound name \"Glass\"" 2>/dev/null || true

exit 0
