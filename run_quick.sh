#!/bin/bash
# run_quick.sh — Morning light-scrape pipeline (run at ~11 AM)
#
# Two parallel lanes (Indeed / LinkedIn): scrape → match that source.
# Job-card digest is sent only after both lanes finish.
#   Indeed:   same 24h search as the full run (site minimum is 1 day),
#             fewer pages per keyword (LIGHT_INDEED_MAX_PAGES).
#   LinkedIn: the 12h quick URL (no keyword loop), LIGHT_LINKEDIN_MAX_PAGES.
#
# Complements the full 24-hour scrape (run_task.sh).
#
# Usage:
#   caffeinate -i ./run_quick.sh          # recommended (keeps Mac awake)
#   ./run_quick.sh                        # without caffeinate

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PROJECT_DIR="/Users/carrienon/Desktop/code-project/job_scraper"
LOG_DIR="$PROJECT_DIR/logs"
PYTHON="/opt/miniconda3/bin/python3"

mkdir -p "$LOG_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_DIR/main.log"
}

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

log "=== Light Scrape Pipeline Started ==="

# ---------------------------------------------------------------------------
# Step 1: Launch Chrome in remote-debug mode.
# Reuse an existing debug Chrome so a second run does not kill the first
# profile and close the tabs the scrapers are using.
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Two parallel lanes: scrape then match that source as soon as it finishes.
# Budgets live in src/core/config.py.
# ---------------------------------------------------------------------------
INDEED_PAGES="$($PYTHON -c "import sys; sys.path.insert(0, 'src'); from core.config import LIGHT_INDEED_MAX_PAGES; print(LIGHT_INDEED_MAX_PAGES)")"
INDEED_JOBS="$($PYTHON -c "import sys; sys.path.insert(0, 'src'); from core.config import INDEED_JOBS_PER_PAGE; print(INDEED_JOBS_PER_PAGE)")"
LINKEDIN_PAGES="$($PYTHON -c "import sys; sys.path.insert(0, 'src'); from core.config import LIGHT_LINKEDIN_MAX_PAGES; print(LIGHT_LINKEDIN_MAX_PAGES)")"
LINKEDIN_JOBS="$($PYTHON -c "import sys; sys.path.insert(0, 'src'); from core.config import LINKEDIN_JOBS_PER_PAGE; print(LINKEDIN_JOBS_PER_PAGE)")"
STAMP="$(date +%Y%m%d_%H%M%S)"

log "Step 1/1: Light lanes — Indeed ${INDEED_PAGES} pages/keyword × ${INDEED_JOBS}, LinkedIn quick ${LINKEDIN_PAGES} pages × ${LINKEDIN_JOBS}"
run_source_lane "Indeed" "indeed" \
  "$LOG_DIR/indeed_light_${STAMP}.log" "$LOG_DIR/matcher_indeed_light_${STAMP}.log" \
  $PYTHON src/scrapers/indeed_scraper.py \
  --max-pages "$INDEED_PAGES" \
  --max-jobs "$INDEED_JOBS" &
INDEED_LANE=$!
run_source_lane "LinkedIn" "linkedin" \
  "$LOG_DIR/linkedin_quick_${STAMP}.log" "$LOG_DIR/matcher_linkedin_light_${STAMP}.log" \
  $PYTHON src/scrapers/linkedin_quick_scraper.py \
  --max-pages "$LINKEDIN_PAGES" \
  --max-jobs "$LINKEDIN_JOBS" &
LINKEDIN_LANE=$!

log "  Indeed lane (PID: $INDEED_LANE)  and  LinkedIn lane (PID: $LINKEDIN_LANE)  running..."
wait $INDEED_LANE
wait $LINKEDIN_LANE

INDEED_EXIT="$(read_lane_exit indeed scrape)"
QUICK_EXIT="$(read_lane_exit linkedin scrape)"
INDEED_MATCH_EXIT="$(read_lane_exit indeed match)"
LINKEDIN_MATCH_EXIT="$(read_lane_exit linkedin match)"
if [ "$INDEED_MATCH_EXIT" -eq 0 ] && [ "$LINKEDIN_MATCH_EXIT" -eq 0 ]; then
    MATCHER_EXIT=0
else
    MATCHER_EXIT=1
fi

# Close only the Chrome this run started. A reused debug window is left open.
if [ -n "$CHROME_PID" ]; then
    log "Closing Chrome..."
    kill $CHROME_PID 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
log "=== Light Scrape Pipeline Completed ==="
log "Indeed scrape:   $([ "$INDEED_EXIT" -eq 0 ] && echo '✅' || echo '❌')"
log "LinkedIn scrape: $([ "$QUICK_EXIT" -eq 0 ] && echo '✅' || echo '❌')"
log "Indeed match:    $([ "$INDEED_MATCH_EXIT" -eq 0 ] && echo '✅' || echo '❌')"
log "LinkedIn match:  $([ "$LINKEDIN_MATCH_EXIT" -eq 0 ] && echo '✅' || echo '❌')"

# Fetch stats from MongoDB
STATS=$($PYTHON << 'EOF'
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
from core.db_mongo import get_collection
from datetime import datetime, timedelta
try:
    jobs         = get_collection("jobs")
    matched_jobs = get_collection("matched_jobs")
    since = datetime.now() - timedelta(hours=12)
    scraped = jobs.count_documents({"created_at": {"$gte": since}})
    matched = matched_jobs.count_documents({"matched_at": {"$gte": since}})
    print(f"{scraped} {matched}")
except Exception:
    print("0 0")
EOF
)

SCRAPED=$(echo "$STATS" | awk '{print $1}')
MATCHED=$(echo "$STATS" | awk '{print $2}')
log "Last 12 h: ${SCRAPED} jobs scraped, ${MATCHED} high-quality matches"

# Push today's matched jobs to Telegram (works for /quick_jobs, cron, and launchd)
log "Pushing today's matches to Telegram..."
$PYTHON -c "
import sys
sys.path.insert(0, 'src')
from core.match_digest import push_todays_matched_jobs
push_todays_matched_jobs()
" >> "$LOG_DIR/telegram_push_$(date +%Y%m%d).log" 2>&1 \
  && log "✅ Telegram match digest sent" \
  || log "⚠️  WARNING: Telegram match digest failed (see logs/telegram_push_*.log)"

# macOS desktop notification
osascript -e "display notification \"${SCRAPED} new jobs scraped, ${MATCHED} matched\" with title \"Light Scrape Done\" sound name \"Glass\"" 2>/dev/null || true

exit 0
