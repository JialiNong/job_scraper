---
name: job-scraper
description: >-
  Project conventions for the personal job_scraper (Indeed/LinkedIn scrape,
  title filters, Lingua language gate, German language gate, AI matching,
  Telegram bot, Web UI). Use whenever editing scrapers, matching, config,
  shell pipelines, bot commands, docs/, or architecture. Enforces English
  docs/comments and keeps docs/architecture.md in sync with flow changes.
---

# Job Scraper — Project Skill

Read this skill before changing scrape, filter, match, pipeline, or docs behavior.

## Language (hard rule)

All of the following must be **English only**:

- Markdown under `docs/` (architecture, criteria, profile notes, READMEs you add)
- Code comments and docstrings
- Commit messages and PR bodies for this repo
- Log / print messages that explain filter decisions (prefer short English)

Chat replies to the user may follow the user’s language. Repo artifacts stay English.

When translating or rewriting docs, keep technical identifiers unchanged (`run_task.sh`, `german_gate`, collection names, env vars).

## Keep architecture in sync (hard rule)

Canonical flow doc: [`docs/architecture.md`](../../../docs/architecture.md).

**After any change that alters how the system works, update `docs/architecture.md` in the same turn** — do not wait to be asked. Trigger examples:

- New / renamed / removed CLI, shell script step, or Telegram command
- Scraper order, parallelism, time window (24h / 12h), or keyword strategy
- Title filter stages, exclude lists, AI title check behavior
- Language detection (`is_non_english_job_detail`) or German gate (`german_gate.py`)
- Matcher gates, threshold, Special Match rules wiring
- Mongo collections / important fields written by scrapers or matcher
- Web UI funnel metrics that map to pipeline stages

Update checklist:

1. Adjust the relevant mermaid diagram(s) and tables
2. Fix the one-line summary at the bottom if the funnel changed
3. Keep Full vs Quick differences accurate
4. Keep the “two German-related gates” section accurate if either gate changed

If the change is docs-only typography with no flow change, skip the architecture update.

## Matching source of truth

- Scoring rules live in [`docs/matching_criteria.md`](../../../docs/matching_criteria.md).
- Candidate profile lives in `docs/user_profile.md`.
- Do **not** invent hard gates or Special Match rules only in `ai_matcher.py` prompts. If scoring logic changes, update `matching_criteria.md` first (or together), then align prompt text and any code gates (`german_gate.py`, `stack_gate.py`).

## Pipeline mental model (do not confuse)

One-line funnel (must stay true in architecture + code):

> Title gates (exclude → DEFAULT_KEYWORDS → AI title) → click detail → non-English JD discarded → English JD saved → matcher empty-desc skip → mandatory-German rule gate → stack extract + backend/AI-ML local gate → AI score → ≥ threshold → `matched_jobs`.

### Two German-related gates

| Stage | Module | Question | Outcome |
|-------|--------|----------|---------|
| Scrape | `scraper_utils.is_non_english_job_detail` | Is the JD body non-English? | Do **not** save; count `german_filtered` |
| Match | `matching.german_gate` | Does an **English** JD require German as a must-have language? | Local reject; skip full AI scoring |

Location / German company / “nice to have German” must **not** fail the match gate.

### Shared LinkedIn card path

`linkedin_quick_scraper` reuses `linkedin_scraper.scrape_jobs()`. Title + language filters must stay shared unless you intentionally split them and document that in architecture.

## Entry points

| Entry | Role |
|-------|------|
| `run_task.sh` / Telegram `/jobs` | Full: Indeed + LinkedIn (24h) parallel lanes (scrape → match that source + Telegram ping) → unmatched desc cleanup → Telegram match digest |
| `run_quick.sh` / Telegram `/quick_jobs` | Light: Indeed 24h (2 pages/keyword) + LinkedIn 12h quick URL (3 pages) parallel lanes (scrape → match that source + Telegram ping) → Telegram match digest |
| `start_ui.sh` | Flask tracker UI (default port 5050) |
| `src/bot/telegram_bot.py` | `/start` `/test` `/jobs` `/quick_jobs` `/matches` `/indeed_ok` |
| `src/core/match_digest.py` | Shared “today’s matches” Telegram digest (pipeline + `/matches`) |
| `/timeouts` | Review title-qualified scrape timeouts; add to Tracker as pending |
| Individual `src/scrapers/*.py`, `src/matching/ai_matcher.py` | Debug / partial runs |

When you change `run_task.sh` / `run_quick.sh`, check Telegram handlers still point at the right scripts and still describe timing/behavior correctly.

## Code conventions

- **Python 3**, package imports via `src/` on `sys.path` (existing scraper/matcher pattern).
- Prefer shared helpers in `src/core/scraper_utils.py` and config in `src/core/config.py` over copying filter logic into each scraper.
- Playwright talks to an existing Chrome via CDP (`CDP_URL` / port 9222). Do not close the user’s Chrome session from scrapers.
- Fail-open where the current code already does (e.g. AI title check / language too-short → allow) unless the user asks to tighten.
- Keep changes scoped: no drive-by refactors; no new markdown files unless asked (architecture updates are required when flow changes).
- Do not commit unless the user asks.

## Config knobs (where to edit)

| Concern | Location |
|---------|----------|
| Search keywords | `DEFAULT_KEYWORDS` in `src/core/config.py` |
| Title blacklist | `TITLE_EXCLUDE_KEYWORDS` in `src/core/config.py` |
| Indeed / LinkedIn / Quick URLs & selectors | `*_CONFIG` in `src/core/config.py` |
| Match threshold / model | `.env` (`MATCH_THRESHOLD`, `AI_MODEL`, `OPENAI_API_KEY`) |
| Telegram destination | `.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`, `TELEGRAM_CHAT_ID`, `TELEGRAM_MESSAGE_THREAD_ID`) |
| Scoring policy | `docs/matching_criteria.md` |

## Suggested agent workflow for feature work

1. Read this skill + skim `docs/architecture.md` for the affected stage.
2. Implement the code / script / bot change.
3. Update `docs/architecture.md` if flow/commands/filters/gates changed.
4. If scoring rules changed, update `docs/matching_criteria.md` and keep `ai_matcher` / `german_gate` aligned.
5. Briefly tell the user what flow docs were updated.

## Out of scope reminders

- Personal use scrapers — respect site ToS; no credential dumping into git.
- Never commit `.env` or secrets.
