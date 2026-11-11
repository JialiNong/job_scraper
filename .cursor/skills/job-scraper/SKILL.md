---
name: job-scraper
description: >-
  Project conventions for the personal job_scraper (Indeed/LinkedIn scrape,
  title filters, Lingua language gate, AI matching (model labels + code score),
  Telegram bot, Web UI). Use whenever editing scrapers, matching, config,
  shell pipelines, bot commands, docs/, architecture, or Tracker UI
  (templates/static CSS/JS). Enforces English docs/comments, phone vs desktop
  interaction parity, unified phone control sizing, a UI chrome check
    (`./scripts/check_ui.sh`) after template/CSS/JS edits, keeps
    docs/architecture.md in sync with flow changes, and requires an impact
    check of related UI/data/filters/environments in the same PR.
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
| UI | desktop + mobile (~375px), toolbar consistency with sibling pages; see **Web UI** below |
| Environments | local vs Render (no Playwright on Render; lazy imports so Save Link Only / Flask can start without it) |
| Tests | the affected neighbors, not only the new happy path |

**Grep** every touched function, field, and component name before finishing. In the PR body and the final report, list the related areas you checked.

A short always-applied pointer lives in [`.cursor/rules/impact-check.mdc`](../../rules/impact-check.mdc).

## Web UI (hard rule)

Apply whenever editing Tracker templates, `static/css/*`, or `static/js/*` — not only when the user mentions mobile.

### Phone vs desktop interaction

Phones have **no hover**. Do not ship desktop-only affordances (`:hover` tooltips, hover-to-reveal detail, click-immediately-navigates) without a touch path.

| Desktop | Phone (default) |
|---------|-----------------|
| Hover shows detail / count / preview | Single tap shows the same detail |
| Click navigates or opens a filtered view | Double-tap (or an explicit link/button) navigates |
| Copy says “hover” | Copy says “tap” (`TOUCH_UI` / `POINT_VERB` / `[data-point-verb]` in `static/js/index.js`) |

Patterns already in the Tracker:

- Detect touch with `TOUCH_UI` / `@media (hover: none)` (not merely `max-width`)
- Dual hints: `.cal-hint-hover` / `.cal-hint-touch` (and similar) so copy matches the input mode
- Heatmap: tap → day panel; double-tap → that day’s applied filter; desktop keeps hover + click-to-open

When adding a new hover-driven control, implement the touch equivalent **in the same PR** and update visible hints.

### Unify control sizing (do not patch one button)

If the user points at one tall / short / uneven control on phone, **do not only fix that node**. Treat it as a density/token problem for the whole surface.

1. Prefer shared tokens — phone control height is `--phone-ctrl-h` in `static/css/base.css` (currently `30px`).
2. Grep sibling controls on the same page and parallel pages (`index`, `unmatched`, `timeouts`, `manual_apply`): toolbar buttons, selects, filter chips, Save/Add, close (×), batch bar, list row actions.
3. Remove one-off `min-height: 44px` (or similar) touch mins that fight the shared token unless there is an explicit a11y reason.
4. Verify ~375px width: controls should share one visual height rhythm, not a mix of “fixed this one” outliers.

Same idea for color/weight (e.g. pale Save buttons): match the established solid primary style across Save / Add / confirm actions, not only the button named in chat.

### UI chrome check (different layer from E2E)

`tests/e2e` is for **data**: click a real flow, then assert MongoDB. Do not put overlay / viewport / select-centering contracts there.

After any edit under `templates/`, `static/css/`, or `static/js/`, run **`./scripts/check_ui.sh`** in the same turn (throwaway mongod + server; never the real DB). It checks:

| Surface | Phone (~390×844) | Desktop (~1280×800) |
|---------|------------------|---------------------|
| Filters | fixed sheet in viewport, scrim, body lock, count label, custom date, Escape + tap-scrim close | absolute dropdown, no scrim/lock, click-outside close |
| Job details | sheet in viewport, mask above it, tap scrim closes | side drawer (`top: 0`), no mask, page still scrolls |
| Log Job / Timeouts selects | shared `--phone-ctrl-h` recipe (appearance none, padding, 16px) | (phone recipe only) |
| Unmatched Filters | same sheet contract + custom score | — |
| Batch bar / timeline modal | bar stays on screen | datetime modal z-index above the drawer |

Look at the screenshots under the path the script prints (`/tmp/job-ui-chrome/` unless `UI_CHECK_OUT` is set). A green exit code is not a substitute for a visual miss (text sitting high, mask too faint).

If the script fails, fix the UI and re-run before finishing. Mention the result in the final report.

### First-load request order

On Tracker cold entry, **`/api/jobs` is critical path**. Chips (`/api/stats`) and Overview (journey / heatmap / locations) load **after** the first job list (`loadAfterJobList` / `listReady` in `static/js/index.js`). Do not reintroduce parallel `/api/stats` on init without a reason.

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
3. If UI changed: apply **Web UI** (touch vs hover, unify `--phone-ctrl-h` / sibling controls, keep job-list-first loading), then run `./scripts/check_ui.sh`.
4. Update `docs/architecture.md` if flow/commands/filters/gates changed.
5. If matching changed, keep `docs/matching_rules.md`, the schema in `evaluator.py`, and `MATCH_WEIGHTS` aligned, and run `scripts/tools/eval_matcher.py`.
6. Impact check: grep usages of every touched function / field / component; update neighbors from the checklist in the same PR.
7. Briefly tell the user what flow docs were updated and which related areas you checked.

## Out of scope reminders

- Personal use scrapers — respect site ToS; no credential dumping into git.
- Never commit `.env` or secrets.
