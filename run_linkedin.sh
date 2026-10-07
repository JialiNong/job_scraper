#!/bin/bash
# run_linkedin.sh — LinkedIn-only scrape + AI match with a flexible time window.
#
# Same keyword loop / page budget as the full LinkedIn lane (DEFAULT_KEYWORDS,
# FULL_MAX_PAGES, LINKEDIN_JOBS_PER_PAGE), but f_TPR is chosen per run.
#
# Usage:
#   caffeinate -i ./run_linkedin.sh                 # default: last 24 hours
#   caffeinate -i ./run_linkedin.sh --hours 4       # last 4 hours
#   caffeinate -i ./run_linkedin.sh --hours 2 -p 3  # 2h, override pages
#   ./run_linkedin.sh --hours 48 -p 3 -j 30         # explicit pages + cards
#
# Options:
#   --hours N   Posted-within window in hours (default: 24). Examples: 2, 4, 12, 48
#   -p / --max-pages N   Pages per keyword (default: FULL_MAX_PAGES from config)
#   -j / --max-jobs N    Cards per page (default: LINKEDIN_JOBS_PER_PAGE)
#   -h / --help

PROJECT_DIR="/Users/carrienon/Desktop/code-project/job_scraper"
LOG_DIR="$PROJECT_DIR/logs"
PYTHON="/opt/miniconda3/bin/python3"

mkdir -p "$LOG_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_DIR/main.log"
}

trap 'log "ERROR: Script failed at line $LINENO"' ERR

HOURS=24
MAX_PAGES=""
MAX_JOBS=""

usage() {
    sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --hours)
            HOURS="$2"
            shift 2
            ;;
        --hours=*)
            HOURS="${1#*=}"
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
            # Positional fallback: ./run_linkedin.sh 4
            if [[ "$1" =~ ^[0-9]+([.][0-9]+)?$ ]] && [ -z "${HOURS_SET:-}" ]; then
                HOURS="$1"
                HOURS_SET=1
                shift
            else
                log "Unknown argument: $1"
                usage 1
            fi
            ;;
    esac
done

# Validate hours (positive number)
if ! "$PYTHON" -c "h=float('$HOURS'); raise SystemExit(0 if h > 0 else 1)"; then
    log "Invalid --hours value: $HOURS (need a positive number)"
    exit 1
fi

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

log "=== LinkedIn ${HOURS}h Scrape + Match Started ==="

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
    MAX_JOBS="$($PYTHON -c "import sys; sys.path.insert(0, 'src'); from core.config import LINKEDIN_JOBS_PER_PAGE; print(LINKEDIN_JOBS_PER_PAGE)")"
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
SCRAPE_LOG="$LOG_DIR/linkedin_${HOURS}h_${STAMP}.log"
MATCH_LOG="$LOG_DIR/matcher_linkedin_${HOURS}h_${STAMP}.log"

log "LinkedIn lane — last ${HOURS}h, ${MAX_PAGES} pages/keyword × ${MAX_JOBS} cards"
run_source_lane "LinkedIn (${HOURS}h)" "linkedin" \
  "$SCRAPE_LOG" "$MATCH_LOG" \
  $PYTHON src/scrapers/linkedin_scraper.py \
  --hours "$HOURS" \
  --max-pages "$MAX_PAGES" \
  --max-jobs "$MAX_JOBS"

LINKEDIN_EXIT="$(read_lane_exit linkedin scrape)"
LINKEDIN_MATCH_EXIT="$(read_lane_exit linkedin match)"

if [ -n "$CHROME_PID" ]; then
    log "Closing Chrome..."
    kill $CHROME_PID 2>/dev/null || true
fi

log "=== LinkedIn ${HOURS}h Pipeline Completed ==="
log "LinkedIn scrape: $([ "$LINKEDIN_EXIT" -eq 0 ] && echo '✅' || echo '❌')"
log "LinkedIn match:  $([ "$LINKEDIN_MATCH_EXIT" -eq 0 ] && echo '✅' || echo '❌')"

STATS=$($PYTHON << EOF
import sys, os
sys.path.insert(0, os.path.join("$PROJECT_DIR", "src"))
from core.db_mongo import get_collection
from datetime import datetime, timedelta
try:
    jobs         = get_collection("jobs")
    matched_jobs = get_collection("matched_jobs")
    since = datetime.now() - timedelta(hours=float("$HOURS"))
    scraped = jobs.count_documents({"source": "linkedin", "created_at": {"\$gte": since}})
    matched = matched_jobs.count_documents({"source": "linkedin", "matched_at": {"\$gte": since}})
    print(f"{scraped} {matched}")
except Exception:
    print("0 0")
EOF
)

SCRAPED=$(echo "$STATS" | awk '{print $1}')
MATCHED=$(echo "$STATS" | awk '{print $2}')
log "Last ${HOURS} h (LinkedIn): ${SCRAPED} jobs scraped, ${MATCHED} high-quality matches"

log "Pushing today's matches to Telegram..."
$PYTHON -c "
import sys
sys.path.insert(0, 'src')
from core.match_digest import push_todays_matched_jobs
push_todays_matched_jobs()
" >> "$LOG_DIR/telegram_push_$(date +%Y%m%d).log" 2>&1 \
  && log "✅ Telegram match digest sent" \
  || log "⚠️  WARNING: Telegram match digest failed (see logs/telegram_push_*.log)"

osascript -e "display notification \"LinkedIn ${HOURS}h: ${SCRAPED} scraped, ${MATCHED} matched\" with title \"LinkedIn Scrape Done\" sound name \"Glass\"" 2>/dev/null || true

exit 0
