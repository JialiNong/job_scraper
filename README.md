# Job Scraper - AI-Powered Job Search Assistant

Automatically scrapes job listings from Indeed and LinkedIn, uses AI to score each match, and notifies you daily.

### Two-pipeline workflow

| Pipeline | Script | When to run | What it does |
|---|---|---|---|
| **Full scrape** | `run_task.sh` | ~6 PM daily | Indeed + LinkedIn (last 24 h), all keywords, AI matching |
| **Light scrape** | `run_quick.sh` | ~11 AM daily | Indeed (24 h, 2 pages/keyword) + LinkedIn quick URL (12 h, 3 pages), then AI matching |

Run the full scrape every evening so the database is refreshed.  
Run the light scrape every morning before your application session to capture the freshest listings.

## Features

- **Multi-platform scraping** – Indeed and LinkedIn, runs sequentially
- **AI matching** – OpenAI scores each job 0–10 against your profile
- **Smart deduplication** – already-analyzed jobs are skipped automatically
- **Human-like behavior** – random delays to avoid detection
- **Daily automation** – macOS LaunchD triggers it every morning
- **MongoDB storage** – all data persisted for querying

---

## Project Structure

```
job_scraper/
├── src/
│   ├── core/                          # Shared infrastructure
│   │   ├── config.py                  # Central configuration
│   │   ├── db_mongo.py                # MongoDB operations
│   │   └── scraper_utils.py           # Shared utilities
│   ├── scrapers/                      # Job scrapers
│   │   ├── indeed_scraper.py          # Indeed scraper (24-hour window)
│   │   ├── linkedin_scraper.py        # LinkedIn scraper (24-hour window)
│   │   ├── linkedin_quick_scraper.py  # LinkedIn quick scraper (12-hour window)
│   │   └── manual_apply_scraper.py    # Manual job URL logger (default: Not Applied)
│   ├── matching/
│   │   └── ai_matcher.py              # AI matching engine
│   ├── web/
│   │   └── web_app.py                 # Flask Job Tracker UI
│   └── bot/
│       └── telegram_bot.py            # Telegram bot
├── docs/
│   ├── user_profile.md                # Your resume — required for AI
│   └── matching_criteria.md           # Your job criteria — required for AI
├── templates/                         # Web UI templates
├── logs/                              # Log output directory
├── run_task.sh                        # Full pipeline (6 PM — Indeed + LinkedIn 24 h)
├── run_quick.sh                       # Light pipeline (11 AM — Indeed 2 pages + LinkedIn 12 h)
├── start_ui.sh                        # Start Job Tracker web UI
├── com.user.job_scraper.plist         # LaunchD config (daily schedule)
└── .env                               # Environment variables (not in git)
```

---

## Setup

### 1. Install dependencies

```bash
pip3 install -r requirements.txt
playwright install chromium
```

### 2. Configure `.env`

```bash
# MongoDB
MONGO_URI=mongodb+srv://username:password@cluster.mongodb.net/

# OpenAI
OPENAI_API_KEY=sk-proj-your_key_here
AI_MODEL=gpt-4o-mini
MATCH_THRESHOLD=7.0
```

### 3. Fill in your profile

- `docs/user_profile.md` — your resume and skills
- `docs/matching_criteria.md` — your must-haves and red flags

### 4. Test run

```bash
# Quick smoke test — 1 page each, 3 jobs analyzed
python3 src/scrapers/indeed_scraper.py -k "frontend" -p 1
python3 src/scrapers/linkedin_scraper.py -k "frontend" -p 1
python3 src/matching/ai_matcher.py -l 3

# Test quick scraper (1 page, 5 jobs)
python3 src/scrapers/linkedin_quick_scraper.py -p 1 -j 5

# View results (web UI)
./start_ui.sh
```

---

## Daily Automation (macOS LaunchD)

### Full pipeline — 6 PM daily

```bash
cp com.user.job_scraper.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.job_scraper.plist
```

Runs every day at **6:00 PM**. A desktop notification pops up when done.

### Quick pipeline — 11 AM daily

Create `com.user.job_quick_scraper.plist` alongside the existing one:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.user.job_quick_scraper</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/Users/carrienon/Desktop/code-project/job_scraper/run_quick.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>11</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>/Users/carrienon/Desktop/code-project/job_scraper/logs/launchd_quick_stdout.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/carrienon/Desktop/code-project/job_scraper/logs/launchd_quick_stderr.log</string>
</dict>
</plist>
```

```bash
cp com.user.job_quick_scraper.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.job_quick_scraper.plist
```

### Change the schedule

Edit the relevant `.plist` file:

```xml
<key>Hour</key>
<integer>18</integer>  <!-- 18 = 6 PM (full pipeline) -->
<key>Minute</key>
<integer>0</integer>
```

Then reload:

```bash
# Full pipeline
launchctl unload ~/Library/LaunchAgents/com.user.job_scraper.plist
launchctl load  ~/Library/LaunchAgents/com.user.job_scraper.plist

# Quick pipeline
launchctl unload ~/Library/LaunchAgents/com.user.job_quick_scraper.plist
launchctl load  ~/Library/LaunchAgents/com.user.job_quick_scraper.plist
```

### Common commands

```bash
# Run immediately
launchctl start com.user.job_scraper        # full pipeline
launchctl start com.user.job_quick_scraper  # quick pipeline

# Pause automation
launchctl unload ~/Library/LaunchAgents/com.user.job_scraper.plist
launchctl unload ~/Library/LaunchAgents/com.user.job_quick_scraper.plist

# Resume automation
launchctl load ~/Library/LaunchAgents/com.user.job_scraper.plist
launchctl load ~/Library/LaunchAgents/com.user.job_quick_scraper.plist

# Check status
launchctl list | grep job_scraper
```

### Keep the laptop awake

LaunchD won't fire if the Mac is asleep. To prevent idle sleep while the lid is **open**:

```bash
# Never idle-sleep when on AC power (lid-close still sleeps normally)
sudo pmset -c sleep 0 displaysleep 10
```

This means: screen turns off after 10 minutes of inactivity, but the system stays awake until you close the lid. Restore defaults any time:

```bash
sudo pmset -c sleep 1 displaysleep 10
```

---

## Running the Pipelines Manually

### Full pipeline (~6 PM — 24-hour window)

```bash
caffeinate -i ./run_task.sh
```

Execution order:

1. Launch Chrome (remote debug mode)
2. Indeed scraper — scrapes new jobs (last 24 hours)
3. LinkedIn scraper — scrapes new jobs (last 24 hours, iterates keywords)
4. AI matcher — analyzes all unprocessed jobs
5. Desktop notification — shows counts
6. Chrome closed

Estimated time: **40–60 minutes**. Full run: 3 pages per keyword, Indeed ~15 cards/page, LinkedIn 30.

### Light pipeline (~11 AM)

```bash
caffeinate -i ./run_quick.sh
```

Execution order:

1. Launch or reuse Chrome (remote debug mode)
2. In parallel:
   - Indeed — same last-24-hours search as the full run (Indeed cannot filter shorter than 1 day), **2 pages per keyword**, ~15 cards/page
   - LinkedIn quick — jobs posted in the **last 12 hours**, one pre-built OR search URL (Full Stack Engineer / Frontend Developer / Product Engineer / Generative AI Engineer), **3 pages × 30 cards**
3. AI matcher — analyzes all unprocessed jobs
4. Desktop notification — shows counts
5. Chrome closed only if this run started it

Estimated time: **20–40 minutes**.

### Recommended daily schedule

| Time | Command | Purpose |
|------|---------|---------|
| **~6 PM** | `caffeinate -i ./run_task.sh` | Refresh DB with all new jobs from the past 24 hours |
| **~11 AM** | `caffeinate -i ./run_quick.sh` | Catch jobs posted overnight before applying |

---

## Configuration

### Keywords — `src/core/config.py`

```python
DEFAULT_KEYWORDS = [
    "frontend",
    "full stack",
    "full-stack",
    "fullstack",
    "ai engineer",
    "product engineer",
    "software engineer",
]
```

### Scraping limits

```python
FULL_MAX_PAGES = 3              # Full run: pages per keyword (both platforms)
INDEED_JOBS_PER_PAGE = 15       # Indeed cards per page (also the start= step)
LINKEDIN_JOBS_PER_PAGE = 30     # LinkedIn cards per page
LIGHT_INDEED_MAX_PAGES = 2      # Light run: Indeed pages per keyword
LIGHT_LINKEDIN_MAX_PAGES = 3    # Light run: LinkedIn quick URL pages
```

`run_task.sh` and `run_quick.sh` read these values at start.

### Match threshold — `.env`

```bash
MATCH_THRESHOLD=7.0   # Only save jobs scoring ≥ this value
```

Raise it (e.g. `8.0`) for fewer, higher-quality results. Lower it (e.g. `6.0`) to cast a wider net.

---

## CLI Reference

### Scrapers

| Option | Short | Default | Applies to |
|--------|-------|---------|-----------|
| `--keywords` | `-k` | `DEFAULT_KEYWORDS` | `indeed_scraper`, `linkedin_scraper` |
| `--max-pages` | `-p` | full `3` / LinkedIn quick `3` | all scrapers |
| `--max-jobs` | `-j` | Indeed `15`, LinkedIn `30` | all scrapers |

```bash
python3 src/scrapers/indeed_scraper.py -k "react developer" -p 2
python3 src/scrapers/linkedin_scraper.py -k "frontend" "full stack" -p 3

# LinkedIn light URL — no keywords; pre-built 12-hour search
python3 src/scrapers/linkedin_quick_scraper.py              # 3 pages, 30 jobs/page
python3 src/scrapers/linkedin_quick_scraper.py -p 2         # 2 pages
python3 src/scrapers/linkedin_quick_scraper.py -p 3 -j 20   # 3 pages, 20 jobs each
```

### AI Matcher

| Option | Short | Default |
|--------|-------|---------|
| `--limit` | `-l` | all |
| `--source` | `-s` | all |
| `--threshold` | `-t` | `7.0` |

```bash
python3 src/matching/ai_matcher.py -l 5              # test with 5 jobs
python3 src/matching/ai_matcher.py -s indeed -t 7.5  # Indeed only, stricter
```

---

## Database Schema

### `jobs` collection

```javascript
{
  job_id:      "abc123",
  title:       "Senior Frontend Developer",
  company:     "Tech GmbH",
  location:    "Berlin, Germany",
  source:      "linkedin",          // "indeed" or "linkedin"
  link:        "https://...",
  description: "...",
  status:      "new",
  created_at:  ISODate("2026-09-21"),
  matched_at:  ISODate("2026-09-21"),  // set after AI analysis
  match_score: 8.5
}
```

### `matched_jobs` collection

Jobs that scored ≥ threshold:

```javascript
{
  job_id:               "abc123",
  title:                "Senior Frontend Developer",
  company:              "Tech GmbH",
  match_score:          8.5,
  recommendation:       "Yes",
  match_reasons:        ["Strong React match", "Remote-friendly"],
  missing_requirements: ["5+ years preferred"],
  red_flags:            [],
  summary:              "Excellent frontend role",
  status:               "pending",   // pending → applied → rejected
  matched_at:           ISODate("2026-09-21"),
  applied_at:           null,
  notes:                ""
}
```

Update a job after applying:

```python
from core.db_mongo import get_collection

matched = get_collection("matched_jobs")
matched.update_one(
    {"job_id": "abc123", "source": "linkedin"},
    {"$set": {"status": "applied", "notes": "Applied via LinkedIn"}}
)
```

---

## Viewing Results

```bash
./start_ui.sh                   # open Job Tracker web UI
```

---

## Logs

```bash
tail -f logs/main.log                       # pipeline summary (both pipelines)
tail -f logs/indeed_YYYYMMDD.log            # Indeed detail
tail -f logs/linkedin_YYYYMMDD.log          # LinkedIn full-scrape detail
tail -f logs/linkedin_quick_YYYYMMDD.log    # LinkedIn quick-scrape detail
tail -f logs/matcher_YYYYMMDD.log           # AI matcher (full pipeline)
tail -f logs/matcher_quick_YYYYMMDD.log     # AI matcher (quick pipeline)
tail -f logs/launchd_stderr.log             # LaunchD errors

# Clean up logs older than 7 days
find logs/ -name "*.log" -mtime +7 -delete
```

---

## Troubleshooting

### Chrome won't connect

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="/tmp/chrome_selenium"
```

### LinkedIn / Indeed login expired

Manually reopen Chrome with the scraper's profile and log in again:

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --user-data-dir="/tmp/chrome_selenium"
```

Then visit linkedin.com / indeed.com, log in, and tick **"Keep me logged in"**. Sessions typically last ~30 days.

### LaunchD not firing

```bash
launchctl list | grep job_scraper        # check loaded
tail -f logs/launchd_stderr.log          # check errors
./run_task.sh                            # manual test
```

### MongoDB connection failed

```bash
python3 -c "import sys; sys.path.insert(0,'src'); from core.db_mongo import init_db; init_db(); print('OK')"
```

Check `MONGO_URI` in `.env` and ensure your IP is whitelisted in MongoDB Atlas.

### No new jobs to process

All existing jobs already have `matched_at` set. Run the scrapers first to fetch today's listings:

```bash
python3 src/scrapers/indeed_scraper.py
python3 src/scrapers/linkedin_scraper.py
```

---

## AI Matching Cost

Using `gpt-4o-mini` (recommended):

- ~$0.001–0.003 per job
- **100 jobs ≈ $0.10–0.30 / day**

Using `gpt-4o`:

- ~$0.02–0.05 per job
- **100 jobs ≈ $2–5 / day**

Monitor usage at https://platform.openai.com/usage.

---

## Notes

- **Personal use only** — respect Indeed and LinkedIn Terms of Service
- Built-in delays: 4–8 s between jobs, 15–30 s between pages
- Chrome uses `/tmp/chrome_selenium` as its profile directory
