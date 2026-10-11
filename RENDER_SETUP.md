# Render Deployment Guide - Environment Variables & Setup

## Environment Variables to Set in Render Dashboard

When deploying to Render, you must configure these environment variables in the Render dashboard (Dashboard → your service → Environment):

### Required Variables

| Variable Name | Example Value | Where to Get It | Notes |
|--------------|---------------|-----------------|-------|
| `TRACKER_USERNAME` | `carrie` | Choose any username | Your login username |
| `TRACKER_PASSWORD_HASH` | `$2b$12$abc...xyz` | Run `python3 scripts/generate_password_hash.py` locally | Never use plain text password! |
| `MONGO_URI` | `mongodb+srv://user:pass@cluster.mongodb.net/job_scraper` | MongoDB Atlas → Connect → Drivers | Full connection string with credentials |

### Auto-Generated (No Action Needed)

| Variable Name | Value | Notes |
|--------------|-------|-------|
| `FLASK_SECRET_KEY` | (auto-generated) | Render generates this via `render.yaml` |
| `RENDER` | `true` | Render sets this automatically |

### Optional (Only if Using Web-Based Job Logging)

| Variable Name | Example Value | Where to Get It | Notes |
|--------------|---------------|-----------------|-------|
| `OPENAI_API_KEY` | `sk-proj-abc...xyz` | OpenAI Platform → API Keys | Only needed if logging jobs via web UI |
| `AI_MODEL` | `gpt-4o-mini` | — | Default is already set in `render.yaml` |
| `MATCH_THRESHOLD` | `7.0` | — | Default is already set in `render.yaml` |

## Important: MongoDB Atlas Network Access

**You must allow Render to connect to MongoDB Atlas:**

1. Go to MongoDB Atlas Dashboard
2. Click **Network Access** (left sidebar)
3. Click **Add IP Address**
4. Choose **Allow Access from Anywhere**: `0.0.0.0/0`
   - ⚠️ **Why?** Render's free plan has no static IP addresses
   - Your database is still secure (requires username/password from `MONGO_URI`)
5. Click **Confirm**

## Deployment Checklist

- [ ] Generate password hash locally: `python3 scripts/generate_password_hash.py`
- [ ] Get MongoDB connection string from Atlas dashboard
- [ ] Allow `0.0.0.0/0` in MongoDB Atlas Network Access
- [ ] Push code to GitHub
- [ ] Create Render account at [render.com](https://render.com)
- [ ] New Blueprint → Connect GitHub → Select repository
- [ ] Set environment variables in Render dashboard
- [ ] Deploy and wait ~2-3 minutes
- [ ] Test: visit `https://your-app.onrender.com/health` (should return `{"status":"ok"}`)
- [ ] Test login at `https://your-app.onrender.com/login`

## How It Works

### Architecture

```
┌─────────────────────┐         ┌──────────────────────┐         ┌─────────────┐
│   Mac (Local)       │         │  Render (Free)       │         │  MongoDB    │
│                     │         │                      │         │  Atlas      │
│  • Scrapers         │────────▶│  • Flask Web UI      │────────▶│             │
│  • LaunchD          │  writes │  • Gunicorn (4 wkrs) │  R/W    │  • Jobs DB  │
│  • Chrome           │  jobs   │  • Health check      │  status │             │
│  • Playwright       │         │  • ProxyFix (HTTPS)  │         │             │
└─────────────────────┘         └──────────────────────┘         └─────────────┘
                                          │
                                          ├─────► 📱 Phone
                                          └─────► 💻 Browser
```

### What Runs Where

**Mac (Local):**
- Job scrapers (Indeed, LinkedIn) - need Chrome/Playwright
- AI matcher - scores jobs with OpenAI
- Writes to MongoDB Atlas

**Render (Cloud):**
- Flask web UI only
- Reads jobs from MongoDB
- Updates application status
- No scraping, no Playwright, no Chrome

**MongoDB Atlas (Cloud):**
- Central database
- Both Mac and Render connect to it
- Data syncs automatically

## Gotchas & Common Issues

### 1. MongoDB Connection Fails

**Problem:** App can't connect to MongoDB

**Solutions:**
- ✅ Check Network Access allows `0.0.0.0/0`
- ✅ Verify `MONGO_URI` format: `mongodb+srv://username:password@cluster...`
- ✅ Ensure username/password don't have special characters (URL encode if needed)
- ✅ Check MongoDB Atlas user has read/write permissions

### 2. Login Fails

**Problem:** Can't log in even with correct password

**Solutions:**
- ✅ Verify `TRACKER_PASSWORD_HASH` is the bcrypt hash (starts with `$2b$12$`)
- ✅ Check `TRACKER_USERNAME` matches exactly (case-sensitive)
- ✅ Regenerate hash: `python3 scripts/generate_password_hash.py`
- ✅ Copy entire hash including `$2b$12$` prefix

### 3. Cold Starts (Free Plan)

**Problem:** First request after 15 minutes is slow

**Expected Behavior:**
- Render free plan spins down after 15 min idle
- First request: ~20-30 seconds (waking up)
- Subsequent requests: instant
- Not a bug - this is how free tier works

**Solution:** Upgrade to paid plan for 24/7 uptime (optional)

### 4. 502 Bad Gateway

**Problem:** Render shows 502 error

**Solutions:**
- Check Render logs (Dashboard → Logs)
- Common causes:
  - Missing environment variables
  - MongoDB connection failed
  - App crashed during startup
- Health check should pass at `/health`

### 5. Session Issues

**Problem:** Getting logged out frequently

**Solutions:**
- ✅ `FLASK_SECRET_KEY` must be consistent (don't regenerate it)
- ✅ Enable cookies in your browser
- ✅ Check browser privacy settings allow third-party cookies

## Testing Locally with Gunicorn

Before deploying, test the production setup locally:

```bash
# 1. Install dependencies
pip3 install -r requirements-web.txt

# 2. Set environment variables
export PORT=8080
export TRACKER_USERNAME=test
export TRACKER_PASSWORD_HASH='$2b$12$...'  # your hash
export MONGO_URI='mongodb+srv://...'

# 3. Start with gunicorn (same as Render)
gunicorn --bind 0.0.0.0:$PORT --workers 4 src.web.web_app:app

# 4. Test in browser
open http://localhost:8080
```

If this works, Render deployment will work too.

## Monitoring & Logs

**View Logs:**
- Render Dashboard → your service → Logs
- Real-time tail of application output
- Shows startup, requests, errors

**Health Check:**
- Render pings `/health` every 60 seconds
- Returns `{"status": "ok", "service": "job-tracker"}`
- If health check fails 3 times, Render restarts the service

**Metrics:**
- Render Dashboard → your service → Metrics
- CPU, memory, requests per second
- Free plan: limited metrics

## Cost

- **Render Free Plan:** $0/month
  - 750 hours/month free compute
  - Spins down after 15 min idle
  - HTTPS included
  - No credit card required

- **MongoDB Atlas Free Tier:** $0/month
  - 512MB storage (plenty for personal use)
  - Shared cluster
  - No credit card required

- **Total Cost:** $0/month

## Security Notes

1. **Never commit secrets:** `.env` is in `.gitignore`
2. **Use bcrypt hashes:** Never store plain text passwords
3. **HTTPS by default:** Render provides automatic TLS certificates
4. **Session security:** Cookies are HttpOnly and Secure in production
5. **MongoDB security:** Username/password + network access control

## Support

- **Render Docs:** [render.com/docs](https://render.com/docs)
- **MongoDB Atlas:** [docs.atlas.mongodb.com](https://docs.atlas.mongodb.com/)
- **Project Issues:** Check `DEPLOYMENT_REPORT.md` for more details
