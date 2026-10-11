# Job Tracker - Deployment & Testing Report

## Testing Summary

### Mobile Viewport Testing (390px × 844px - iPhone size)

✅ **All Tests Passed**

#### Authentication
- Login page loads correctly at mobile viewport
- Form fields functional and easy to interact with
- Sign In button prominently displayed and tap-friendly
- Session-based authentication working correctly
- Redirects unauthenticated users to login page

#### Mobile Layout Quality

**Excellent mobile responsiveness:**

1. **Readability**
   - All text clearly legible at 390px width
   - Good font sizing for mobile devices
   - Proper contrast with dark theme
   - Clear visual hierarchy

2. **Touch Targets**
   - All buttons appropriately sized for finger taps (44px minimum)
   - Header navigation buttons well-spaced: "Log Job", "Timeouts", "Unmatched Jobs", "Refresh", "Logout"
   - Status filter chips are large and easy to tap
   - Form inputs have 16px font size (prevents iOS zoom)

3. **Layout**
   - ✅ **Zero horizontal scrolling**
   - Content fits perfectly within 390px width
   - All elements properly contained within viewport
   - Responsive grid adjusts to single column on mobile
   - Stacked filters and controls for easy access

4. **Screenshots**
   - Login page: `artifacts/login-mobile.png`
   - Tracker dashboard: `artifacts/tracker-mobile.png`

## Current Deployment Architecture

### Local Setup (Current)
- **Scrapers**: Run on macOS via LaunchD
  - Indeed scraper (24h window, 3 pages/keyword)
  - LinkedIn scraper (24h window, 3 pages/keyword)
  - Requires Chrome/Playwright (cannot run on most cloud hosts)
- **Web UI**: Flask dev server on `127.0.0.1:5050`
  - Only accessible from local machine
- **Database**: MongoDB Atlas (cloud-hosted)

### Recommended Mobile-Accessible Setup

**Keep scrapers local, deploy only the web UI:**

```
┌─────────────┐         ┌──────────────┐         ┌─────────────┐
│   Mac       │         │  Railway /   │         │  MongoDB    │
│  (Local)    │────────▶│  Render      │────────▶│  Atlas      │
│             │         │  (Web UI)    │         │  (Cloud)    │
│ • Scrapers  │         │              │         │             │
│ • LaunchD   │         │ • Read-only  │         │ • Job data  │
│ • Chrome    │         │ • Track jobs │         │ • Matches   │
│             │         │ • Update     │         │             │
└─────────────┘         └──────────────┘         └─────────────┘
                                │
                                │
                           ┌────▼─────┐
                           │  Phone   │
                           │  Browser │
                           └──────────┘
```

## Deployment Options

### Option 1: Railway (Recommended)

**Pros:**
- Free tier available
- Simple Python deployment
- Automatic HTTPS
- Easy environment variable management

**Setup:**
1. Create `Procfile`:
   ```
   web: python src/web/web_app.py --host 0.0.0.0 --port $PORT
   ```
2. Push to GitHub
3. Connect Railway to repository
4. Add environment variables in Railway dashboard
5. Deploy

**Required Environment Variables:**
- `MONGO_URI` - MongoDB Atlas connection string
- `TRACKER_USERNAME` - Login username
- `TRACKER_PASSWORD_HASH` - Bcrypt password hash
- `FLASK_SECRET_KEY` - Session encryption key
- `MATCH_THRESHOLD=7.0`
- `AI_MODEL=gpt-4o-mini`
- `OPENAI_API_KEY` - Only needed if adding manual job logging via web

**What Won't Work:**
- Job scrapers (need Chrome/Playwright)
- Keep running these locally on Mac

### Option 2: Render

Similar to Railway:
1. Create `render.yaml`:
   ```yaml
   services:
     - type: web
       name: job-tracker
       env: python
       buildCommand: "pip install -r requirements.txt"
       startCommand: "python src/web/web_app.py --host 0.0.0.0 --port $PORT"
   ```
2. Add same environment variables
3. Deploy from GitHub

### Option 3: Fly.io

**Pros:**
- More control
- Good free tier
- Global edge deployment

**Setup:**
1. Install Fly CLI
2. Run `fly launch` in project directory
3. Configure `fly.toml` automatically
4. Set secrets: `fly secrets set TRACKER_USERNAME=...`
5. Deploy: `fly deploy`

### Option 4: Ngrok (Quick Testing)

**For temporary mobile access without deployment:**

```bash
# Terminal 1: Start web UI
./start_ui.sh

# Terminal 2: Create public tunnel
ngrok http 5050
```

Ngrok provides a temporary public URL (e.g., `https://abc123.ngrok.io`). Free tier has session limits.

## Required Accounts

- **GitHub**: Already have (repository owner)
- **Railway/Render/Fly**: Sign up for one (free tier available)
- **MongoDB Atlas**: Already configured
- **OpenAI**: Already configured

## Environment Variables Reference

### Required for Authentication
```bash
TRACKER_USERNAME=your_username
TRACKER_PASSWORD_HASH=<generate_with_script>
FLASK_SECRET_KEY=<random_hex_string>
```

### Generate Password Hash
```bash
python3 scripts/generate_password_hash.py
```

### Generate Secret Key
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

## Security Notes

1. **Never commit secrets** - `.env` is in `.gitignore`
2. **Use bcrypt for passwords** - Plain text passwords never stored
3. **HTTPS in production** - Railway/Render/Fly provide this automatically
4. **Session security** - Flask-Login with secure session cookies
5. **Single user** - This is a personal tool, designed for one user

## Data Flow

1. **Scraping** (Local Mac):
   - LaunchD triggers `run_task.sh` / `run_quick.sh`
   - Chrome + Playwright scrape Indeed/LinkedIn
   - Save jobs to MongoDB Atlas
   - AI matcher scores jobs
   - Telegram notifications

2. **Tracking** (Web UI - can be deployed):
   - Read job data from MongoDB Atlas
   - Update application status
   - Track interview progress
   - Accessible from any device with web browser

## Next Steps for Deployment

1. Choose hosting platform (Railway recommended)
2. Generate production password hash
3. Generate production Flask secret key
4. Add environment variables to hosting platform
5. Deploy web UI
6. Keep Mac scrapers running locally
7. Access tracker from phone via deployed URL

## Cost Estimates

- **MongoDB Atlas**: Free tier (sufficient for personal use)
- **Railway/Render**: Free tier includes enough for this app
- **Fly.io**: Free tier works for this use case
- **OpenAI API**: Already using for matching (cost unchanged)

Total additional cost for mobile access: **$0** (using free tiers)
