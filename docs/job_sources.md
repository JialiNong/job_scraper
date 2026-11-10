# Job Sources — every way a job gets into Job Scraper

This page lists **all the ways jobs reach the database / Tracker**, what each one is good for, how it runs, and how to set it up. For the internal filter → match flow see [`architecture.md`](./architecture.md).

---

## 1. Overview

There are **5 ways to get jobs**, plus 2 ways to rescue jobs the pipeline already saw.

| # | Way | Sources | Runs where / when | Needs | `jobs.source` | Tracker `origin` |
|---|-----|---------|-------------------|-------|---------------|------------------|
| 1 | **Browser scrapers** | LinkedIn, Indeed | Your Mac, on demand (Telegram `/jobs` → `scripts/run_jobs.sh`) | Mac on, debug Chrome, sometimes an Indeed captcha | `linkedin`, `indeed` | `pipeline` |
| 2 | **Public job APIs** | Arbeitnow, Bundesagentur für Arbeit (Adzuna: opt-in, paid) | GitHub Actions, **daily 06:00 UTC** (Mac can be off), or by hand | Nothing (Adzuna: paid key) | `arbeitnow`, `arbeitsagentur`, `adzuna` | `api` |
| 3 | **Company ATS boards** | Greenhouse, Lever, Ashby, Personio, Recruitee, Workable — for companies on the watchlist | Same daily GitHub Actions run | Watchlist `config/ats_companies.json` | `ats` (+ `ats_provider`, `ats_slug`) | `api` |
| 4 | **Manual Log Job** | Any job URL you paste | Tracker web UI **Log Job**, or `src/scrapers/manual_apply_scraper.py` | — | `linkedin` / `indeed` / `manual` | `manual` |
| 5 | **Agent push** | Jobs an AI agent (Claude, …) recommends | `POST /api/agent/jobs` (Render) or `scripts/tools/log_recommended_jobs.py` | `AGENT_API_TOKEN` | from the URL | `agent` |

Rescue paths (not new sources):

- **Unmatched → Can Apply**: a job the matcher scored below 7.0 that you still want → copied to the Tracker.
- **Timeouts → Add as Not Applied**: a job whose detail page did not load during scraping.

Ways 1–3 go through the same filters and the same AI matcher. Only jobs scoring ≥ `MATCH_THRESHOLD` (7.0) appear on the Tracker; the rest stay on Unmatched. Ways 4–5 put the job on the Tracker directly.

---

## 2. Which way finds what

| Way | Strength | Weakness |
|-----|----------|----------|
| LinkedIn / Indeed | Biggest volume, English startup roles | Fragile (DOM changes, captchas), against the sites' ToS, needs the Mac |
| Arbeitnow | Full JD, tech-focused, English-heavy, no key | Small (~100 jobs/day across all of Europe), no search; ~50% are outside Germany |
| Bundesagentur | Most complete German job database | Mostly German-language postings (dropped by the English gate), many traditional employers |
| Adzuna (opt-in) | Proper search API across many boards | **Paid** (25 trial calls, then £8/month); only a ~500-char teaser, so the full JD comes from following the link (best effort) |
| Company ATS | First-hand data, full JD, new roles show up before the boards | Only covers companies you put on the watchlist |
| Log Job / agent | Anything you or an agent find elsewhere | Manual |

**Language:** the Bundesagentur returns mostly German postings. The search uses the English `DEFAULT_KEYWORDS`, which already favours English postings. Any German JD is dropped by the language gate after the free detail call, before any AI cost. In the first dry run (1 day): 39 listings → 14 dropped by title blacklist, 14 by AI title check, 4 German, 1 duplicate, 1 saved (Zalando).

---

## 3. API sources and ATS boards (ways 2 + 3)

Code: `src/sources/` — one module per source, one shared funnel.

```
src/sources/
├── run_sources.py      # entry point: fetch → funnel → AI match → Telegram
├── base.py             # SourceJob + shared funnel (ingest)
├── http_client.py      # stdlib HTTP client (no requests / Playwright)
├── arbeitnow.py        # free feed, no search, 300 jobs per page
├── arbeitsagentur.py   # BA v6 search + v4 detail (full JD)
├── adzuna.py           # opt-in; needs ADZUNA_APP_ID / ADZUNA_APP_KEY (paid)
└── ats.py              # 6 ATS providers + watchlist loader
config/ats_companies.json      # ATS watchlist
scripts/tools/discover_ats.py  # find a company's ATS board
```

### 3.1 How each source is queried

| Source | Request | Time window | Notes |
|--------|---------|-------------|-------|
| Arbeitnow | `GET arbeitnow.com/api/job-board-api?page=N` | pages newest-first until older than `--days` | No search on the server: every listing is filtered locally (title must look like a dev role, location must be Germany/remote) |
| Bundesagentur | `GET rest.arbeitsagentur.de/…/pc/v6/jobs?was=<keyword>&veroeffentlichtseit=<d>&pav=false&zeitarbeit=false` (header `X-API-Key: jobboerse-jobsuche`) then `pc/v4/jobdetails/<base64 refnr>` for the JD | `veroeffentlichtseit` only accepts 0/1/7/14/28, so the window is rounded up and also checked locally | `pav=false` drops private recruiting agencies, `zeitarbeit=false` drops temp staffing. v4 search is gone (403); v6 search has no description. The API is free and public but has no official docs (bundesAPI's community docs) |
| Adzuna | `GET api.adzuna.com/v1/api/jobs/de/search/N?what_phrase=<keyword>&max_days_old=<d>` | `max_days_old` | Teaser only: the funnel follows `redirect_url` and extracts the page text (trafilatura). If it is still short, the job is saved as `description_incomplete` and the matcher skips it at no cost |
| ATS | one request per watched company (see table below) | none: boards list only open roles | Every run fetches every open role. Roles already in the DB are skipped, so only new openings cost anything |

| ATS provider | Endpoint |
|--------------|----------|
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` |
| Lever (`lever` / `lever_eu`) | `api.lever.co/v0/postings/{slug}?mode=json` / `api.eu.lever.co/…` |
| Ashby | `api.ashbyhq.com/posting-api/job-board/{slug}` |
| Personio | `{slug}.jobs.personio.de/xml?language=en` (`"host": "jobs.personio.com"` to override) |
| Recruitee | `{slug}.recruitee.com/api/offers/` |
| Workable | `apply.workable.com/api/v1/widget/accounts/{slug}?details=true` |

### 3.2 Funnel (`sources/base.py: ingest`)

Cheapest checks first. The full JD is fetched, and the AI title check paid for, only after the free checks pass:

1. **Location**: per-source policy
   - `strict` (ATS: global companies): must name Germany or a German city, or be remote with no country (or EU/EMEA scope). Rejects "Austin, Texas" and "Remote - US".
   - `lenient` (Arbeitnow): the Tracker's `is_germany_or_remote`.
   - `none` (Bundesagentur, Adzuna DE): already Germany-only.
2. **Title blacklist**: shared `TITLE_EXCLUDE_KEYWORDS` + API-only `API_TITLE_EXCLUDE_EXTRA` (VP, Director, New Grad, Internship, Werkstudent, …). The API-only list does not change LinkedIn / Indeed.
3. **On-topic title** (feeds without search: Arbeitnow, ATS): `FEED_TITLE_INCLUDE_PATTERNS` (engineer / developer / Entwickler / frontend / …).
4. **Dedup**: `job_id` already known, the same company + title seen earlier in this run (one role posted for 5 cities), or the same `dedup_key` already in `jobs`.
5. **Cross-source dedup**: same normalised company + title already saved from another source (e.g. LinkedIn). Skipped and counted as "dup of other board". Only the API sources check this; LinkedIn / Indeed behave as before.
6. **AI title check**: same as the browser scrapers (auto-pass keywords skip it).
7. **Fetch JD** if the listing did not include it → completeness flags.
8. **English only**: Lingua; non-English → `german_filtered`.
9. **Save** to `jobs` (`status=new`). The normal AI matcher then scores it.

### 3.3 Running it

```bash
python3 src/sources/run_sources.py                              # Arbeitnow + Bundesagentur + ATS, last 1 day, match + Telegram
python3 src/sources/run_sources.py --sources arbeitnow,arbeitsagentur,ats,adzuna   # include Adzuna (needs key)
python3 src/sources/run_sources.py --sources arbeitnow,ats --days 3
python3 src/sources/run_sources.py --dry-run                    # print what would be saved; no writes, no Telegram
python3 src/sources/run_sources.py --no-match --no-telegram     # save only
```

Telegram gets one summary (`🌐 API sources done … • Arbeitnow: 259 listed, 4 new saved, …`) and then this run's match cards. Cards for jobs from these sources include the company and the source label. The Tracker and Unmatched pages show the same label as a coloured ◆ badge: **ATS** (teal), **Bundesagentur** (red), **Arbeitnow API** (blue), **Adzuna API** (amber). Hover the badge to see the site, e.g. `jobs.ashbyhq.com`. The Source filter has the same four options.

---

## 4. Automatic daily run (no Mac needed)

`.github/workflows/api-sources.yml` runs on GitHub's servers every day at 06:00 UTC (07:00 Berlin in winter). It runs `run_sources.py`, which writes to the same MongoDB Atlas database the Tracker on Render reads. You can also start it by hand: GitHub → **Actions → API job sources → Run workflow** (choose days / sources).

The LinkedIn / Indeed lane cannot run there (it needs your logged-in Chrome and sometimes a human for Indeed's captcha), so it stays on Telegram `/jobs` on the Mac.

### One-time setup

1. **Push the branch / merge to `main`**. Scheduled workflows only run from the default branch.
2. GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**:

   | Secret | Value | Required |
   |--------|-------|----------|
   | `MONGO_URI` | same as `.env` | yes |
   | `OPENAI_API_KEY` | same as `.env` | yes |
   | `USER_PROFILE_MD` | the full contents of `docs/user_profile.md` (your CV; gitignored, so the runner needs it as a secret) | yes |
   | `TELEGRAM_BOT_TOKEN` | bot token from @BotFather (same as `.env`) | for Telegram |
   | `TELEGRAM_CHAT_ID` | the group the cards go to, e.g. `-100…` (same as `.env`) | for Telegram |
   | `TELEGRAM_MESSAGE_THREAD_ID` | forum topic id (Job Assistance) inside that group (same as `.env`) | optional: without it, messages go to the group's General topic |
   | `TELEGRAM_ALLOWED_USER_ID` | your personal Telegram user id (same as `.env`); only used as the chat when `TELEGRAM_CHAT_ID` is missing | optional |
   | `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | from developer.adzuna.com (**paid**: 25 trial calls, then £8/month) | optional: only if you also add `adzuna` to the run's sources |
   | `AI_MODEL`, `MATCH_THRESHOLD` | same as `.env` | optional |

3. **MongoDB Atlas → Network Access**: GitHub runners have changing IPs, so `0.0.0.0/0` must be allowed. Render already needs this, so it is probably already set.
4. Run it once by hand (**Run workflow**) and check the Telegram summary.

### Registration summary

| Source | Sign up? |
|--------|----------|
| Arbeitnow | No |
| Bundesagentur | No: the public client key `jobboerse-jobsuche` is built in |
| Company ATS boards | No |
| Adzuna | Yes, and **not free**: a new key is "Pending Activation" with 25 test calls; 5,000 calls/month costs £8/month. A daily run uses 6–12 calls, so it is off by default |

### Cost and limits

- GitHub Actions: free for public repos; private repos get 2,000 free minutes/month, and a daily run takes ~2–8 minutes.
- OpenAI: the AI title check costs fractions of a cent per title. The full match (with a company web lookup, cached per company) runs only on saved jobs. The **first ATS run** saves every currently open matching role (~70 in the first dry run). After that only new openings are saved.
- GitHub pauses scheduled workflows in a repo with no commits for 60 days. Any push, or one manual run, re-enables it.

---

## 5. Maintaining the ATS watchlist

The ATS feeds have no cross-company search: you can only ask "what is open at company X". That is why there is a watchlist. You do not have to build it by hand:

```bash
# Seed / refresh from companies already on your Tracker (read-only on MongoDB)
python3 scripts/tools/discover_ats.py --from-tracker --limit 250            # print hits
python3 scripts/tools/discover_ats.py --from-tracker --limit 250 --write    # add hits with DE/remote roles

# Add specific companies
python3 scripts/tools/discover_ats.py "Trade Republic" "Pitch" "Raycast" --write
```

How discovery works: it guesses slugs from the name (`Buying Labs GmbH` → `buyinglabs`, `buying-labs`) and probes all 6 providers. A hit needs at least one open role, plus a matching company name where the provider reports one (Greenhouse, Workable, Recruitee). Several providers answer "200 OK, 0 jobs" for any slug, and short slugs collide ("scalable" is The Scalable Company, not Scalable Capital). **Review hits before `--write`.**

Edit `config/ats_companies.json` by hand to remove a company or pause it:

```json
{"company": "n8n", "provider": "ashby", "slug": "n8n"}
{"company": "Canonical", "provider": "greenhouse", "slug": "canonical", "enabled": false}
```

The initial watchlist (80 companies) was seeded from the Tracker. Recruiters, marketplaces (Clera, Turing, Jobster) and name collisions were removed by hand. Companies with no public board (custom career sites, SmartRecruiters, Workday, JOIN) cannot be added; for those, keep using ways 1, 4 and 5.

Privacy note: the watchlist is committed to the repo. If the repo is public, it shows which companies you track.
