#!/bin/bash
# run_task.sh - Daily job scraping and AI matching pipeline
# Two parallel lanes (Indeed / LinkedIn): scrape → match that source.
# Job-card digest is sent only after both lanes finish.

# Configuration
PROJECT_DIR="/Users/carrienon/Desktop/code-project/job_scraper"
LOG_DIR="$PROJECT_DIR/logs"
PYTHON="/opt/miniconda3/bin/python3"

# Create log directory if it doesn't exist
mkdir -p "$LOG_DIR"

# Logging helper
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_DIR/main.log"
}

# Log errors but don't stop the pipeline on failure
trap 'log "ERROR: Script failed at line $LINENO"' ERR

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

log "=== Job Scraping Pipeline Started ==="

# Step 1: Launch Chrome in remote debug mode (background).
# Reuse an existing debug Chrome so a second run does not kill the first
# profile and close the tabs the scrapers are using.
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

# Wait until the debugging port is actually accepting connections
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

# Two parallel lanes: scrape then match that source as soon as it finishes.
# Page and card budgets live in src/core/config.py.
FULL_PAGES="$($PYTHON -c "import sys; sys.path.insert(0, 'src'); from core.config import FULL_MAX_PAGES; print(FULL_MAX_PAGES)")"
INDEED_JOBS="$($PYTHON -c "import sys; sys.path.insert(0, 'src'); from core.config import INDEED_JOBS_PER_PAGE; print(INDEED_JOBS_PER_PAGE)")"
LINKEDIN_JOBS="$($PYTHON -c "import sys; sys.path.insert(0, 'src'); from core.config import LINKEDIN_JOBS_PER_PAGE; print(LINKEDIN_JOBS_PER_PAGE)")"
STAMP="$(date +%Y%m%d_%H%M%S)"

log "Step 1/3: Full lanes — ${FULL_PAGES} pages/keyword, Indeed ${INDEED_JOBS}/page, LinkedIn ${LINKEDIN_JOBS}/page"
run_source_lane "Indeed" "indeed" \
  "$LOG_DIR/indeed_${STAMP}.log" "$LOG_DIR/matcher_indeed_${STAMP}.log" \
  $PYTHON src/scrapers/indeed_scraper.py \
  --max-pages "$FULL_PAGES" \
  --max-jobs "$INDEED_JOBS" &
INDEED_LANE=$!
run_source_lane "LinkedIn" "linkedin" \
  "$LOG_DIR/linkedin_${STAMP}.log" "$LOG_DIR/matcher_linkedin_${STAMP}.log" \
  $PYTHON src/scrapers/linkedin_scraper.py \
  --max-pages "$FULL_PAGES" \
  --max-jobs "$LINKEDIN_JOBS" &
LINKEDIN_LANE=$!

log "  Indeed lane (PID: $INDEED_LANE)  and  LinkedIn lane (PID: $LINKEDIN_LANE)  running..."
wait $INDEED_LANE
wait $LINKEDIN_LANE

INDEED_EXIT="$(read_lane_exit indeed scrape)"
LINKEDIN_EXIT="$(read_lane_exit linkedin scrape)"
INDEED_MATCH_EXIT="$(read_lane_exit indeed match)"
LINKEDIN_MATCH_EXIT="$(read_lane_exit linkedin match)"
if [ "$INDEED_MATCH_EXIT" -eq 0 ] && [ "$LINKEDIN_MATCH_EXIT" -eq 0 ]; then
    MATCHER_EXIT=0
else
    MATCHER_EXIT=1
fi

# Clear JD text on unmatched jobs older than 14 days (keeps link + AI fields)
log "Step 2/3: Clearing old unmatched job descriptions..."
$PYTHON scripts/cleanup_unmatched_descriptions.py --execute --days 14 > "$LOG_DIR/cleanup_desc_$(date +%Y%m%d).log" 2>&1
CLEANUP_EXIT=$?
if [ $CLEANUP_EXIT -eq 0 ]; then
    log "✅ Unmatched description cleanup completed"
else
    log "⚠️  WARNING: Unmatched description cleanup failed (exit code: $CLEANUP_EXIT)"
fi

# Close only the Chrome this run started. A reused debug window is left open.
if [ -n "$CHROME_PID" ]; then
    log "Closing Chrome..."
    kill $CHROME_PID 2>/dev/null || true
fi

# Pipeline summary
log "=== Pipeline Completed ==="
log "Indeed scrape:   $([ "$INDEED_EXIT" -eq 0 ] && echo '✅' || echo '❌')"
log "LinkedIn scrape: $([ "$LINKEDIN_EXIT" -eq 0 ] && echo '✅' || echo '❌')"
log "Indeed match:    $([ "$INDEED_MATCH_EXIT" -eq 0 ] && echo '✅' || echo '❌')"
log "LinkedIn match:  $([ "$LINKEDIN_MATCH_EXIT" -eq 0 ] && echo '✅' || echo '❌')"
log "Desc Clean:      $([ $CLEANUP_EXIT -eq 0 ] && echo '✅' || echo '❌')"

# Get today's stats from the database
log "Fetching today's stats..."
STATS=$($PYTHON << 'EOF'
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
from core.db_mongo import get_collection
from datetime import datetime
try:
    jobs         = get_collection("jobs")
    matched_jobs = get_collection("matched_jobs")
    today_start  = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    scraped = jobs.count_documents({"created_at": {"$gte": today_start}})
    matched = matched_jobs.count_documents({"matched_at": {"$gte": today_start}})
    print(f"{scraped} {matched}")
except Exception as e:
    print("0 0")
EOF
)

SCRAPED=$(echo "$STATS" | awk '{print $1}')
MATCHED=$(echo "$STATS" | awk '{print $2}')
log "Today: ${SCRAPED} jobs scraped, ${MATCHED} high-quality matches"

# Push today's matched jobs to Telegram only after both lanes finish
log "Step 3/3: Pushing today's matches to Telegram..."
$PYTHON -c "
import sys
sys.path.insert(0, 'src')
from core.match_digest import push_todays_matched_jobs
push_todays_matched_jobs()
" >> "$LOG_DIR/telegram_push_$(date +%Y%m%d).log" 2>&1 \
  && log "✅ Telegram match digest sent" \
  || log "⚠️  WARNING: Telegram match digest failed (see logs/telegram_push_*.log)"

# macOS desktop notification
osascript -e "display notification \"${SCRAPED} new jobs scraped, ${MATCHED} matched\" with title \"Job Scraper Done\" sound name \"Glass\"" 2>/dev/null || true

exit 0
