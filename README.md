# Job Scraper - AI-Powered Job Search Assistant

Collects job listings from LinkedIn and Indeed (browser), public job APIs (Arbeitnow, Bundesagentur für Arbeit; Adzuna opt-in) and company ATS boards (Greenhouse, Lever, Ashby, Personio, Recruitee, Workable), uses AI to score each match, and notifies you on Telegram.

All job sources and their setup: **[docs/job_sources.md](docs/job_sources.md)**.

### How it runs

**API sources: daily in the cloud.** GitHub Actions (`.github/workflows/api-sources.yml`) runs `src/sources/run_sources.py` every morning, so your Mac can stay off. Run it locally with:

```bash
python3 src/sources/run_sources.py              # Arbeitnow + Bundesagentur + ATS, last 1 day
python3 src/sources/run_sources.py --dry-run    # preview, no writes
```

**LinkedIn + Indeed: on demand from Telegram.** These need your Mac's Chrome, so they run with one command:

```
/jobs [days] [linkedin_hours] [pages]
```

Indeed and LinkedIn are scraped in parallel; each source is AI-matched as soon as its scrape finishes, and today's match cards arrive once both are done.

## Features

- **Multi-platform scraping** – Indeed and LinkedIn, in parallel
- **API sources** – Arbeitnow, Bundesagentur and 80 company ATS boards (Adzuna opt-in), daily on GitHub Actions
- **AI matching** – OpenAI scores each job 0–10 against your profile
- **Smart deduplication** – already-analyzed jobs are skipped automatically
- **Human-like behavior** – random delays to avoid detection
- **Telegram control** – start a run with `/jobs` from your phone
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
│   │   ├── indeed_scraper.py          # Indeed scraper (--days window)
│   │   ├── linkedin_scraper.py        # LinkedIn scraper (--hours window)
│   │   └── manual_apply_scraper.py    # Manual job URL logger (default: Not Applied)
│   ├── matching/
│   │   ├── ai_matcher.py              # Matching entry point (pipeline / CLI)
│   │   └── evaluator.py               # Model labels job + company, code scores
│   ├── web/
│   │   ├── web_app.py                 # Flask app entry: config + registers Blueprints
│   │   ├── auth.py                    # Single-user login
│   │   ├── status.py                  # Status / timeline rules (pure)
│   │   ├── list_query.py              # Tracker list filter → Mongo query
│   │   ├── serialize.py               # Mongo doc → card JSON
│   │   ├── chrome_tasks.py            # Background Chrome tasks: state + locks
│   │   └── routes/                    # Blueprints: pages, jobs, unmatched, timeouts,
│   │                                  #   stats, log_job, ai_tasks (no URL prefix)
│   └── tg/                            # Telegram (bot commands + push messages)
│       ├── bot.py                     # Bot: receives /jobs, /indeed_ok
│       ├── client.py                  # Push: Bot API sendMessage helper
│       └── digest.py                  # Today's match cards → Telegram
├── docs/
│   ├── architecture.md                # How the system works (canonical flow doc)
│   ├── user_profile.md                # Your resume — required for AI
│   ├── matching_rules.md              # Rules the model applies (hard fails, labels)
│   ├── deployment/                    # Render setup + deployment report
│   └── archive/testing/               # Past one-off test / verification reports
├── scripts/
│   ├── run_jobs.sh                    # Pipeline behind Telegram /jobs
│   ├── start_ui.sh                    # Start Job Tracker web UI
│   ├── lib/pipeline_common.sh         # Shared shell helpers for run_jobs.sh
│   ├── db/cleanup_excluded_titles.py  # One-off DB cleanup by title blacklist
│   └── tools/                         # eval_matcher, generate_password_hash, log_recommended_jobs
├── evals/                             # Matcher evaluation set + baselines
├── static/                            # Page JS/CSS: css/<page>.css + js/<page>.js per template
│   └── css/base.css, job_list.css     #   shared: colour tokens + common rules (all pages);
│                                      #   job list (Tracker + Unmatched). Load order: base → job_list → page
├── tests/                             # Unit tests (python3 -m unittest discover -s tests)
│   └── e2e/                           # Browser E2E tests for the web UI (./scripts/run_e2e.sh)
├── templates/                         # Web UI templates
├── logs/                              # Log output directory
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

# Web UI Authentication (required)
TRACKER_USERNAME=your_username
TRACKER_PASSWORD_HASH=<generated_hash>

# Optional: Flask secret key (auto-generated if not set)
FLASK_SECRET_KEY=<random_hex_string>
```

### 3. Generate password hash for web UI

The web UI now requires login. Generate a password hash:

```bash
python3 scripts/tools/generate_password_hash.py
```

This will prompt you for a password and output a hash. Copy the `TRACKER_PASSWORD_HASH=...` line to your `.env` file.

**Important**: Never commit your `.env` file or share your password hash!

### 4. Fill in your profile

- `docs/user_profile.md` — your full CV (gitignored; the model reads all of it)
- `docs/matching_rules.md` — hard fails and how jobs are labelled
- `MATCH_WEIGHTS` in `src/core/config.py` — how labels turn into a score (e.g. hardware −2.5, global customers +0.7)

### 5. Test run

```bash
# Quick smoke test — 1 page each, 3 jobs analyzed
python3 src/scrapers/indeed_scraper.py -k "frontend" -p 1
python3 src/scrapers/linkedin_scraper.py -k "frontend" -p 1
python3 src/matching/ai_matcher.py -l 3

# View results (web UI)
./scripts/start_ui.sh
```

---

## Running a scrape

### From Telegram (normal use)

Start the bot (`python3 src/tg/bot.py`), then send:

| Command | Indeed | LinkedIn | Pages per keyword |
|---|---|---|---|
| `/jobs` | last 1 day | last 24 h | config default (3) |
| `/jobs 7` | last 7 days | last 168 h | config default |
| `/jobs 3 120` | last 3 days | last 120 h | config default |
| `/jobs 3 72 2` | last 3 days | last 72 h | 2 |

- `days` must be 1, 3, 7 or 14 (Indeed's date filter).
- `linkedin_hours` defaults to `days × 24`.
- `pages` applies to both platforms; to set it you must also give `days` and `linkedin_hours`.

Other bot commands: `/start`, `/test` (health checks) and `/indeed_ok` (resume Indeed after a human-verification check; the inline **Continue** button does the same).

### From the shell

```bash
caffeinate -i ./scripts/run_jobs.sh                              # both last 24h
caffeinate -i ./scripts/run_jobs.sh --days 3 --hours 72 --pages 2
```

Execution order:

1. Launch or reuse Chrome (remote debug mode)
2. In parallel: Indeed lane and LinkedIn lane — each scrapes, then AI-matches its own source (Telegram ping after each step)
3. Push today's match cards to Telegram
4. Desktop notification — shows counts
5. Chrome closed only if this run started it

Only one run at a time: a second `/jobs` while one is running is refused (`logs/pipeline.pid`).

---

## Tests

| What | Command | Time |
|---|---|---|
| Unit tests | `/opt/miniconda3/bin/python3 -m unittest discover -s tests` | < 1 s |
| Browser E2E (web UI) | `./scripts/run_e2e.sh` (add `-v` for each test name) | ~20 s |

**E2E** (`tests/e2e/`) starts a throwaway MongoDB on a random local port, the real
web server (`src/web/web_app.py`), and headless Google Chrome, then clicks through
login, application progress, Other status, notes, edit, delete, Unmatched → Can Apply,
Timeouts → Add, Log Job (link only) and the agent API, checking MongoDB after each.
Any uncaught JavaScript error fails the test.

- It **never touches your real data**: the harness refuses to start unless the server
  points at `127.0.0.1` + a database ending in `_e2e`, with AI / Telegram keys blank.
- Needs `mongod` (`brew install mongodb/brew/mongodb-community`; no service needs to
  run) and Playwright (already used by the scrapers; it drives your installed Chrome).
- Seed data and fixed card ids live in `tests/e2e/harness.py`. When you change a
  page's buttons or ids, update the selectors in `tests/e2e/test_tracker_e2e.py`.

**Automatic run in Claude Code:** `.claude/settings.json` has a Stop hook
(`scripts/e2e_hook.sh`). When Claude finishes a turn in which `src/web`, `src/core`,
`templates`, `static` or `tests` changed, it runs unit + E2E tests; a failure is sent back to
Claude to fix. Unchanged code is not re-run, and a given failure is reported once.
Last full output: `logs/e2e_last.log`. Review or turn it off with `/hooks`.

## Configuration

### Keywords — `src/core/config.py`

```python
DEFAULT_KEYWORDS = [
    "frontend engineer",
    "full-stack engineer",
    "fullstack engineer",
    "product engineer",
    "software engineer",
    "forward deployed engineer",
]
```

### Scraping limits

```python
FULL_MAX_PAGES = 3              # Default pages per keyword (both platforms)
INDEED_JOBS_PER_PAGE = 15       # Indeed cards per page (also the start= step)
LINKEDIN_JOBS_PER_PAGE = 30     # LinkedIn cards per page
```

`scripts/run_jobs.sh` reads these values at start; `/jobs … [pages]` overrides `FULL_MAX_PAGES` for one run.

### Match threshold — `.env`

```bash
MATCH_THRESHOLD=7.0   # Only save jobs scoring ≥ this value
```

Raise it (e.g. `8.0`) for fewer, higher-quality results. Lower it (e.g. `6.0`) to cast a wider net.

### Matching models — `.env` (optional)

```bash
MATCH_JOB_MODEL=gpt-5.4-mini       # layer 1: label the job against CV + rules
MATCH_COMPANY_MODEL=gpt-5.4-mini   # layer 2: company lookup with web search
MATCH_REASONING=low
```

Check a change against your own Tracker decisions before relying on it:

```bash
python3 scripts/tools/eval_matcher.py                          # applied vs unsuitable sample
python3 scripts/tools/eval_matcher.py --unapplied --limit 100  # recent jobs you did not apply to
```

---

## CLI Reference

### Scrapers

| Option | Short | Default | Applies to |
|--------|-------|---------|-----------|
| `--keywords` | `-k` | `DEFAULT_KEYWORDS` | `indeed_scraper`, `linkedin_scraper` |
| `--max-pages` | `-p` | `3` | all scrapers |
| `--max-jobs` | `-j` | Indeed `15`, LinkedIn `30` | all scrapers |

```bash
python3 src/scrapers/indeed_scraper.py -k "react developer" -p 2
python3 src/scrapers/linkedin_scraper.py -k "frontend" "full stack" -p 3
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
  source:      "linkedin",          // linkedin / indeed / arbeitnow / arbeitsagentur / adzuna / ats
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
./scripts/start_ui.sh                   # open Job Tracker web UI
```

---

## Logs

```bash
tail -f logs/main.log                          # pipeline summary
tail -f logs/indeed_YYYYMMDD_HHMMSS.log        # Indeed scrape detail
tail -f logs/linkedin_YYYYMMDD_HHMMSS.log      # LinkedIn scrape detail
tail -f logs/matcher_indeed_YYYYMMDD_HHMMSS.log    # AI matcher (Indeed lane)
tail -f logs/matcher_linkedin_YYYYMMDD_HHMMSS.log  # AI matcher (LinkedIn lane)

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

Per job, layer 1 uses about 4k input + 0.4k output tokens. Jobs that pass layer 1 also need a company lookup (about 12–14k input tokens plus web-search fees) the first time that company is seen; later jobs from the same company reuse the cached result in `company_profiles`.

Monitor usage at https://platform.openai.com/usage. If the account runs out of credits, matching stops and the remaining jobs are picked up on the next run.

---

## Notes

- **Personal use only** — respect Indeed and LinkedIn Terms of Service
- Built-in delays: 4–8 s between jobs, 15–30 s between pages
- Chrome uses `/tmp/chrome_selenium` as its profile directory

---

## Mobile Access & Deployment

### Web UI Authentication

The job tracker web UI now requires login for security. Access is protected by:
- Session-based authentication using Flask-Login
- Username/password credentials from environment variables
- bcrypt-hashed passwords (never store plain text)
- "Remember me" persistent sessions

The UI is fully **mobile-responsive** and works on phones (375-430px width):
- Touch-friendly buttons and controls
- No horizontal scrolling
- Readable cards and status dropdowns
- Mobile-optimized layout

### Current Deployment

The app currently runs **locally on macOS** via `./scripts/start_ui.sh`:
- Flask dev server on `127.0.0.1:5050`
- Accessed only from the local machine
- Scrapes are started on demand via Telegram `/jobs`
- MongoDB Atlas for database (cloud)

---

## Deploy to Render (Recommended)

Deploy the web UI to Render's free plan for mobile access. **Scrapers stay on your Mac** (they need Chrome/Playwright).

### Prerequisites

1. **MongoDB Atlas Network Access**: Allow Render to connect
   - Go to MongoDB Atlas → Network Access
   - Add IP: `0.0.0.0/0` (allow all) - Render free plan has no static IPs
   - Or use Render's outbound IPs if on paid plan

2. **Generate Password Hash** (locally):
   ```bash
   python3 scripts/tools/generate_password_hash.py
   ```
   Save the hash - you'll add it to Render.

### Deploy Steps

1. **Push to GitHub** (if not already):
   ```bash
   git push origin main
   ```

2. **Create Render Account**: Sign up at [render.com](https://render.com) (free)

3. **New Blueprint** in Render dashboard:
   - Click "New +" → "Blueprint"
   - Connect your GitHub repository
   - Select the `job_scraper` repository
   - Render detects `render.yaml` automatically
   - Click "Apply"

4. **Set Environment Variables** in Render dashboard:
   
   Go to your web service → Environment, and add:
   
   | Variable | Value | Where to Get It |
   |----------|-------|-----------------|
   | `TRACKER_USERNAME` | `your_username` | Choose any username |
   | `TRACKER_PASSWORD_HASH` | `$2b$12$...` | From `generate_password_hash.py` |
   | `MONGO_URI` | `mongodb+srv://user:pass@...` | MongoDB Atlas → Connect → Drivers |
   | `OPENAI_API_KEY` | `sk-proj-...` | (Optional) Only if logging jobs via web UI |
   
   `FLASK_SECRET_KEY` is auto-generated by Render (via `render.yaml`).

5. **Deploy**: Click "Manual Deploy" → "Deploy latest commit"

6. **Wait for Build** (~2-3 minutes):
   - Render installs dependencies
   - Starts gunicorn server
   - Health check passes at `/health`

7. **Access Your Tracker**:
   - Render provides a URL: `https://job-tracker-web-xxxx.onrender.com`
   - Open on your phone browser
   - Log in with your credentials
   - Done! 🎉

### Architecture After Deployment

```
┌─────────────────────┐         ┌──────────────────────┐         ┌─────────────┐
│   Mac (Local)       │         │  Render (Free)       │         │  MongoDB    │
│                     │         │                      │         │  Atlas      │
│  • Indeed scraper   │────────▶│  • Flask Web UI      │────────▶│             │
│  • LinkedIn scraper │  writes │  • Read-only tracker │  reads  │  • Jobs     │
│  • AI matcher       │   jobs  │  • Update status     │  writes │  • Matches  │
│  • Chrome/Playwright│         │  • Mobile access     │ status  │             │
│  • Telegram /jobs   │         │  • Gunicorn          │         │             │
└─────────────────────┘         └──────────────────────┘         └─────────────┘
                                          │
                                          ├─────► 📱 iPhone
                                          ├─────► 📱 Android
                                          └─────► 💻 Any browser
```

### Updating the Deployment

After pushing changes to GitHub:

1. Render auto-deploys from `main` branch (if auto-deploy enabled)
2. Or click "Manual Deploy" in Render dashboard
3. Changes are live in ~2-3 minutes

### Free Plan Limits

- **Spins down after 15 minutes** of inactivity (cold start ~30s on next visit)
- 750 hours/month free (plenty for personal use)
- Automatic HTTPS included
- No credit card required

### Troubleshooting

**Can't connect to MongoDB:**
- Check MongoDB Atlas → Network Access
- Must allow `0.0.0.0/0` (all IPs) for Render free plan
- Verify `MONGO_URI` is correct in Render environment variables

**Login fails:**
- Verify `TRACKER_PASSWORD_HASH` matches your password
- Check `TRACKER_USERNAME` is set correctly
- Try regenerating hash with `scripts/tools/generate_password_hash.py`

**App won't start:**
- Check Render logs: Dashboard → your service → Logs
- Common issues: missing environment variables, wrong MONGO_URI format
- Health check should return `{"status": "ok"}` at `/health`

**Cold starts slow:**
- Normal for free plan (spins down after 15 min idle)
- First request after idle: ~20-30 seconds
- Subsequent requests: instant
- Upgrade to paid plan for 24/7 uptime

---

## Alternative Deployment Options

### Deploying for Mobile Access

To access the tracker from your phone, you need to deploy the web UI to a hosting service:

#### Railway

Railway offers free tier and simple Python deployment:

1. Create `Procfile` in project root:
   ```
   web: gunicorn --bind 0.0.0.0:$PORT --workers 4 src.web.web_app:app
   ```

2. Push to GitHub and connect Railway to your repo
3. Add environment variables in Railway dashboard (same as Render)
4. Deploy

**Note**: Job scrapers (Playwright/Chrome) won't work on Railway. Only deploy the web UI for tracking. Keep scrapers running locally on your Mac.

#### Fly.io

Create `fly.toml` and deploy with `fly launch`. Good for more control.

#### Ngrok (Quick Testing)

For quick temporary mobile access without deployment:

```bash
# Install ngrok: https://ngrok.com/download
./scripts/start_ui.sh  # Start the tracker locally

# In another terminal:
ngrok http 5050
```

Ngrok will give you a public URL (e.g., `https://abc123.ngrok.io`) that you can open on your phone. The free tier has session limits.

### What Needs Your Accounts

- **GitHub**: If deploying from a private repo
- **Railway/Render/Fly**: Sign up for hosting account
- **MongoDB Atlas**: Already configured (connection string in `.env`)
- **OpenAI**: Already configured (API key in `.env`)

### Recommended Setup

**For Personal Use** (what you have now):
- Scrapers run locally on Mac, started via Telegram `/jobs` (need Chrome/Playwright)
- Web UI deployed to Railway/Render for mobile access
- MongoDB Atlas for cloud database
- Both Mac and web UI connect to same MongoDB

This way:
- Mac scrapers collect jobs automatically
- Check and update status from phone anytime via deployed web UI
- All data syncs through MongoDB
