#!/bin/bash
# start_ui.sh - Start the Job Tracker Web UI
PROJECT_DIR="/Users/carrienon/Desktop/code-project/job_scraper"
PYTHON="/opt/miniconda3/bin/python3"
# Avoid 5000: macOS AirPlay Receiver binds *:5000 and returns HTTP 403.
PORT="${1:-5050}"

cd "$PROJECT_DIR" || exit 1

echo "🌐 Starting Job Tracker UI at http://127.0.0.1:${PORT}"
echo "   HTML templates hot-reload automatically — edit & save, browser refreshes."
echo "   Press Ctrl+C to stop."
echo ""

# Open browser after a short delay
(sleep 1.5 && open "http://127.0.0.1:${PORT}") &

exec "$PYTHON" src/web/web_app.py --port "$PORT"
