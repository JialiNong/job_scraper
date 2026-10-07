# Shared helpers for run_task.sh / run_quick.sh / run_linkedin.sh.
# Expects PROJECT_DIR, PYTHON, LOG_DIR, and log() to be set; cwd = PROJECT_DIR.

telegram_status() {
    "$PYTHON" -c "
import sys
sys.path.insert(0, 'src')
from core.telegram_notify import send_telegram_message
send_telegram_message(sys.argv[1])
" "$1" || true
}

lane_exit_file() {
    echo "$LOG_DIR/lane_${1}.${2}"
}

read_lane_exit() {
    local path
    path="$(lane_exit_file "$1" "$2")"
    if [ -f "$path" ]; then
        cat "$path"
    else
        echo "1"
    fi
}

# Scrape one platform, then AI-match only that source.
# Usage: run_source_lane NAME SOURCE SCRAPE_LOG MATCH_LOG -- cmd args...
run_source_lane() {
    local name="$1"
    local source="$2"
    local scrape_log="$3"
    local match_log="$4"
    shift 4

    "$@" > "$scrape_log" 2>&1
    local scrape_exit=$?
    echo "$scrape_exit" > "$(lane_exit_file "$source" scrape)"

    if [ "$scrape_exit" -eq 0 ]; then
        log "✅ ${name} scrape completed"
        telegram_status "🔍 ${name} scrape done — matching started."
    else
        log "⚠️  WARNING: ${name} scrape failed (exit ${scrape_exit})"
        telegram_status "⚠️ ${name} scrape failed (exit ${scrape_exit}) — matching saved jobs anyway."
    fi

    "$PYTHON" src/matching/ai_matcher.py --threshold 7.0 --source "$source" \
        > "$match_log" 2>&1
    local match_exit=$?
    echo "$match_exit" > "$(lane_exit_file "$source" match)"

    if [ "$match_exit" -eq 0 ]; then
        log "✅ ${name} matching completed"
        telegram_status "✅ ${name} matching done."
    else
        log "⚠️  WARNING: ${name} matching failed (exit ${match_exit})"
        telegram_status "⚠️ ${name} matching failed (exit ${match_exit})."
    fi
}
