---
name: job-scraper
description: >-
  Project conventions for the personal job_scraper (Indeed/LinkedIn scrape,
  title filters, Lingua language gate, AI matching (model labels + code score),
  Telegram bot, Web UI). Use whenever editing scrapers, matching, config,
  shell pipelines, bot commands, docs/, or architecture. Enforces English
  docs/comments, keeps docs/architecture.md in sync with flow changes, and
  requires an impact check of related UI/data/filters/environments in the
  same PR.
---

# Job Scraper — Project Skill

Read this skill before changing scrape, filter, match, pipeline, or docs behavior.

## Language (hard rule)

All of the following must be **English only**:

- All user-facing UI text (templates, labels, buttons, tooltips, Telegram messages)
- Markdown under `docs/` (architecture, criteria, profile notes, READMEs you add)
- Code comments and docstrings
- Commit messages and PR bodies for this repo
- Log / print messages that explain filter decisions (prefer short English)

Chat replies to the user may follow the user’s language. Repo artifacts stay English.

When translating or rewriting docs, keep technical identifiers unchanged (`run_jobs.sh`, `evaluator`, collection names, env vars).

## Keep architecture in sync (hard rule)

Canonical flow doc: [`docs/architecture.md`](../../../docs/architecture.md).

**After any change that alters how the system works, update `docs/architecture.md` in the same turn** — do not wait to be asked. Trigger examples:

- New / renamed / removed CLI, shell script step, or Telegram command
- Scraper order, parallelism, time window (24h / 12h), or keyword strategy
- Title filter stages, exclude lists, AI title check behavior
- Language detection (`is_non_english_job_detail`)
- Matcher rules (`docs/matching_rules.md`), label schema, `MATCH_WEIGHTS`, threshold
- Mongo collections / important fields written by scrapers or matcher
- Web UI funnel metrics that map to pipeline stages

Update checklist:

1. Adjust the relevant mermaid diagram(s) and tables
2. Fix the one-line summary at the bottom if the funnel changed
3. Keep Full vs Quick differences accurate
4. Keep the “two German-related gates” section accurate if either gate changed

If the change is docs-only typography with no flow change, skip the architecture update.

## Impact check (hard rule)

Before finishing any feature or change, check existing related functionality and update it **in the same PR**. Do not ship a new field, status, flag, or form control that leaves a neighbor (Clear, filters, counts, merge, Render import path, …) stale for a follow-up request.

This is the usual miss: the new path works in isolation, and an older sibling still assumes the previous shape. Examples from this tracker:

- Log Job gained title / company / location, Recommended by, and status — Clear still reset only the URL box
- Side statuses (`unsuitable` / `closed` / …) stopped matching Interview and other stage filters / counts
- Batch Fetch-details checkboxes gated on `recommended_by` and skipped older untagged link-only stubs
- Link-only save imported Playwright at module load, which breaks on Render

### Checklist (use every row that applies)

| You touched… | Also update / verify in this PR |
|--------------|----------------------------------|
| Form fields | clear / reset, defaults, validation, edit / prefill, save payload, backend persistence, display on cards / detail, search / filters / counts / stats, export |
| New data fields | old records without the field (backfill-free fallbacks), API responses, scrape / fetch merge rules (**do not overwrite user input**) |
| New statuses / flags | filters, counts, badges, stats, timelines |
| UI | desktop + mobile (~375px), toolbar consistency with sibling pages |
| Environments | local vs Render (no Playwright on Render; lazy imports so Save Link Only / Flask can start without it) |
| Tests | the affected neighbors, not only the new happy path |

**Grep** every touched function, field, and component name before finishing. In the PR body and the final report, list the related areas you checked.

A short always-applied pointer lives in [`.cursor/rules/impact-check.mdc`](../../rules/impact-check.mdc).

## Matching source of truth

- The model only **labels** a job (`src/matching/evaluator.py`, rules in [`docs/matching_rules.md`](../../../docs/matching_rules.md)) and looks up the company; **code** turns labels into a score with `MATCH_WEIGHTS` in `src/core/config.py`.
- Candidate CV lives in `docs/user_profile.md` (gitignored).
- Preference changes (e.g. "hardware lower") go into `MATCH_WEIGHTS`, not prompts. New label fields go into both the JSON schema in `evaluator.py` and `matching_rules.md`.
- Backend policy: only explicit required / must / strong non-Node backend (`required_non_js`) fails; listed-only (`implied_non_js`) is a −1 gap; ideally / e.g. / duties / company stack pass.
- Validate rule or weight changes with `python3 scripts/tools/eval_matcher.py` (Tracker decisions) before shipping.

## Pipeline mental model (do not confuse)

One-line funnel (must stay true in architecture + code):

> Title gates (exclude → TITLE_AUTO_PASS_KEYWORDS → AI title) → click detail → non-English JD discarded → English JD saved → matcher empty-desc skip → layer 1 model labels (hard fails quote the JD) → layer 2 company lookup (cached) → code score (`MATCH_WEIGHTS`) → ≥ threshold and company not in `MATCH_BLOCKED_COMPANIES` → `matched_jobs`.

### Two German-related gates

| Stage | Module | Question | Outcome |
|-------|--------|----------|---------|
| Scrape | `scraper_utils.is_non_english_job_detail` | Is the JD body non-English? | Do **not** save; count `german_filtered` |
| Match | `matching.evaluator` layer 1 (`german_required` + quote) | Does an **English** JD require German as a must-have language? | Hard fail (score 1), `match_gate=german` |

Location / German company / “nice to have German” / plus / German-OR-English / basic German / “businesses speak German” in a since-clause must **not** fail the match gate.

## Entry points

| Entry | Role |
|-------|------|
| `scripts/run_jobs.sh` / Telegram `/jobs [days] [linkedin_hours] [pages]` | Indeed (`--days`, 1/3/7/14) + LinkedIn (`--hours`, default days × 24) parallel lanes (scrape → match that source + Telegram ping) → Telegram match digest. `--pages` sets pages/keyword on both (default `FULL_MAX_PAGES`). No DB cleanup step. |
| `./scripts/start_ui.sh` | Flask tracker UI (default port 5050) |
| `src/tg/bot.py` | `/start` `/test` `/jobs` `/indeed_ok` (only `/jobs` starts scrapes) |
| `src/tg/digest.py` | Shared “today’s matches” Telegram digest (pushed by `run_jobs.sh`) |
| `/timeouts` | Review title-qualified scrape timeouts; add to Tracker as pending |
| Individual `src/scrapers/*.py`, `src/matching/ai_matcher.py` | Debug / partial runs |

There is no cron / launchd schedule in this project; do not add one. When you change `scripts/run_jobs.sh`, check the Telegram `/jobs` handler still passes the right flags and describes behavior correctly.

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
| Titles clicked without AI check | `TITLE_AUTO_PASS_KEYWORDS` in `src/core/config.py` (not `software engineer`) |
| Title blacklist | `TITLE_EXCLUDE_KEYWORDS` in `src/core/config.py` |
| Tracker company blocklist | `MATCH_BLOCKED_COMPANIES` in `src/core/config.py` (Instaffo / Jobgether) |
| Indeed / LinkedIn URLs & selectors | `*_CONFIG` in `src/core/config.py` |
| Scrape window / pages | `/jobs [days] [linkedin_hours] [pages]` → `run_jobs.sh --days --hours --pages`; default pages `FULL_MAX_PAGES` in `src/core/config.py` |
| Match threshold / models | `.env` (`MATCH_THRESHOLD`, `MATCH_JOB_MODEL`, `MATCH_COMPANY_MODEL`, `MATCH_REASONING`, `OPENAI_API_KEY`); `AI_MODEL` is only the scrape-time title check |
| Match score weights | `MATCH_WEIGHTS` in `src/core/config.py` |
| Telegram destination | `.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`, `TELEGRAM_CHAT_ID`, `TELEGRAM_MESSAGE_THREAD_ID`) |
| Matching rules (model) | `docs/matching_rules.md` |

## Suggested agent workflow for feature work

1. Read this skill + skim `docs/architecture.md` for the affected stage.
2. Implement the code / script / bot change.
3. Update `docs/architecture.md` if flow/commands/filters/gates changed.
4. If matching changed, keep `docs/matching_rules.md`, the schema in `evaluator.py`, and `MATCH_WEIGHTS` aligned, and run `scripts/tools/eval_matcher.py`.
5. Impact check: grep usages of every touched function / field / component; update neighbors from the checklist in the same PR.
6. Briefly tell the user what flow docs were updated and which related areas you checked.

## Out of scope reminders

- Personal use scrapers — respect site ToS; no credential dumping into git.
- Never commit `.env` or secrets.
