# Job Matching Criteria

Score 0–10. Apply the steps in order. Each rule is stated once.

## Candidate

- **Languages**: English (professional). Chinese (native). Does **not** speak German.
- **Frontend**: 7 years with React, Angular, Vue, and strong vanilla JavaScript / ES6+. Also TypeScript, Tailwind, Next.js, Nuxt, Webpack, Vite.
- **Backend**: 1 year full-stack in the Node.js ecosystem (Express, Nest, Fastify, Koa, TypeScript/JavaScript). A little Python — **not** production backend. Treat Python like Java/Go when it is a hard or implied backend requirement. Does **not** know Java, Go, PHP, Ruby, C#, .NET, Scala, Rust, or Kotlin as a backend.
- **Also on resume**: MongoDB, Git, Sentry, Jest, Cypress, Playwright, AI-assisted development (using Copilot / ChatGPT / product LLM features). Not an AI/ML engineer: no model training, fine-tuning, or ML-platform work.
- **Background**: B2B SaaS, B2C e-commerce, startups, and food compliance / regulatory. Consumer apps, admin tools, high-traffic work (3M+ daily views), UX, complex forms, roles and permissions, product-management UIs, component libraries / design systems, performance optimization.

## Step 1 — Hard gates

If **any** gate fails: `match_score` ≤ 3, `recommendation` **No**, `special_match` false. Do not apply bonuses or Special Match.

The **German gate**, **backend-stack gate**, and **AI/ML-core gate** also stop the rest of the evaluation: do not score remaining stack, years, domain, or Special Match.

### 1. German language (check first)

Non-English postings (mostly German; French etc. rare) are already filtered out before matching and counted under German Filtered. Do **not** re-filter because the JD looks German, and do **not** use keyword lists.

**Fail** only if the JD **explicitly** states that German is a mandatory job language (must-have / required / fluent / native / C1 / B2+ for the role). An English JD can still do this in writing — only then fail.

**Do not infer German from location or office.** These pass:

- "Berlin, Germany (On-site)", "Germany (Hybrid)", "DACH"
- "Berlin office", "in-person culture in Berlin", "office in Germany", public-transport / gym perks
- The company is German, European, or based in Berlin / Munich / Hamburg
- The product serves a German/European market or German customers/brands

**Also pass** if German is only "nice to have" / "plus" / "advantage" / "preferred", appears only in the company description, or is never mentioned as a language skill. Do **not** invent a German requirement the JD never wrote.

### 2. Backend language

In code this is a pre-AI extract + local gate (`stack_gate.py`). The extract labels each backend language as `required`, `implied`, `or_list`, `nice_to_have`, `company_uses`, or `willingness`. Do **not** wait for the words must-have / required. A language in Requirements / What you'll bring / Our stack for **this role** is hard even without that wording.

**Production backends the candidate has:** Node.js / TypeScript-backend / JavaScript-backend (Express, Nest, Fastify, Koa, and similar).

**Not production (treat as a gap):** Python, Django, Flask, FastAPI, Java, Go, PHP, Ruby, C#, .NET, Scala, Rust, Kotlin, Spring.

**Fail** if any **hard** backend ask (`required` or `implied`) is a language the candidate cannot do in production. AND means fail even when a known language is listed next to an unknown one:

- "Python, TypeScript, and modern web technologies" (Python is a hard backend ask)
- "Working experience in a JVM backend — Kotlin or Java with Spring"
- "Strong Python skills, it's the primary language"

**OR-list is different.** **Pass** when at least one option is a production backend the candidate has:

- "Backend in Node.js or Python"
- "Experience with Python and/or Node.js"
- "Experience with TypeScript, Python or similar modern languages"

Duties / tech dumps are **not** hard AND by themselves. Prefer the Requirements wording when both appear:

- "Work across TypeScript, Python, React and modern full-stack technologies" (What you'll do) **plus** "TypeScript, Python or similar" (What we're looking for) → **OR, pass**
- Do not fail just because a duties line lists Python next to TypeScript with "and"

**Also pass** when:

- No backend language is required
- Backend is only nice-to-have
- The role is frontend; an unknown language is `company_uses` (another team's stack, e.g. "you own the React dashboard; our backend is Go")
- The unknown language is only `willingness` / learn-on-the-job / "basic knowledge or exposure" (typical junior listings)

Do not keep mixed AND-stack jobs for review and do not floor them at 7.0.

### 3. Years of experience

**Fail** if a **must-have** asks for more years than the candidate has in that dimension:

- Overall / frontend / software **> 7 years**
- Backend-specific **> 1 year**. A range that includes 1 (for example "1–2 years") **passes**.

Do not fail on the title "Senior" alone when the stated years are in range. Nice-to-have year requirements do not fail this gate. 

### 4. DevOps / operations

**Fail** if the title includes DevOps, SRE, or Infrastructure; the JD says DevOps or SRE experience is required / mandatory; or infrastructure / operations is a core responsibility (more than half the job).

**Pass** if DevOps/SRE is only nice-to-have, the JD only mentions basic CI/CD, Docker, or Git, or there is no explicit DevOps requirement.

### 5. AI / ML core role

**Fail** if the job is primarily AI/ML *engineering* — building, training, or operating models — not a product/frontend/fullstack role that happens to use AI.

Typical fails: AI Engineer, Machine Learning Engineer, LLM Engineer, MLOps, Applied AI / NLP / Prompt Engineer as the role; core work is training or fine-tuning models, research, ML platforms, or owning an LLM stack (PyTorch / TensorFlow / CUDA / training pipelines).

**Pass** if AI is a product feature or a coding aid:

- AI-assisted development, Copilot, ChatGPT
- Frontend/fullstack at an AI company (UI on top of existing models)
- Shipping practical LLM product loops (prompt → structured output → human review) as one feature of a web product

## Step 2 — Ordinary score

Skip this step if a hard gate failed. The base score uses **only** skills marked required / mandatory / must-have. Missing nice-to-have skills never reduce it.

### Base score

- **8**: 90%+ of required skills match, and Special Match A/B/C does not apply
- **7**: 70–89% match
- **6**: 50–69% match
- **5**: 40–49% match
- **4**: 30–39% match

### Frontend

**Full match** if the required frontend is any of:

- React ecosystem: React, Next.js, Redux, React Query, Zustand, React Router, and similar
- Vue ecosystem: Vue, Nuxt, Vuex, Pinia, Vue Router, and similar
- Angular ecosystem: Angular, RxJS, NgRx, Angular Material, and similar
- TypeScript, JavaScript, Tailwind, CSS-in-JS, Webpack, Vite
- Vanilla / framework-agnostic JavaScript as the primary ask (vanilla JS, DOM, browser APIs)

**Partial match** if the framework is a less common or legacy one (CanJS, Backbone, Ember, Knockout, MooTools, and similar) and either the JD also lists React, Angular, Vue, or "similar" technologies, or strong JavaScript is the real requirement and the framework is secondary. Do not disqualify. Start from a base around 7 and deduct at most 0.5–1 for the framework gap. A named legacy framework is not Special Match A.

**No deduction** for these equivalents: Webpack / Vite / npm scripts ≈ Grunt / Gulp. Jest / Cypress / Playwright ≈ Mocha / Chai.

### Other required technologies

Deduct inside the base score **only** when the JD makes one of these mandatory and it is not on the resume:

- Specialized databases and enterprise stacks: Oracle, SQL Server, DB2, SAP, mainframe
- Specialized data systems: Hadoop, Spark, Kafka
- Niche technology outside web development

Do **not** deduct for nice-to-have items, close neighbors (PostgreSQL next to MongoDB), or common web tools (Redis, GraphQL).

### Bonuses

Add these only for an ordinary match. Special Match replaces the score in Step 3 instead of stacking them. After bonuses, cap an ordinary score at **8.0**.

Nice-to-have / plus / bonus / preferred — add only skills the candidate **has**, never subtract for ones they lack:

- +0.5 for 1–2 skills
- +1.0 for 3–4
- +1.5 for 5+
- +2.0 for most or all

Typical nice-to-haves: an extra language, non-required DevOps, a cloud certification, a test tool beyond Jest/Cypress, Figma or Sketch, a project-management tool, unrelated domain knowledge.

Domain:

- +1.0 for e-commerce, SaaS, or food compliance
- +0.5 for a related domain
- +0 for an unrelated domain

Also treat these as positive fit when the JD asks for them (they use the bonuses above, not a separate score): component libraries or design systems, performance optimization, AI-assisted development, English plus remote, startup or scale-up.

## Step 3 — Special Match (9–10)

If A, B, or C matches, set `special_match: true`, fill `special_match_reasons`, and **replace** the score with **9.0**. Do not leave these at 8. Otherwise `special_match: false` and `special_match_reasons: []`.

Do not be conservative on A/B/C. Hard gates override this step. If a gate in Step 1 already failed, do not apply A, B, or C.

### A. Pure frontend, stack matches → 9.0

- The role is frontend-only (Frontend Developer / Engineer, UI Engineer, JavaScript Developer). Not backend-heavy (more than about 70% backend work), not DevOps/SRE.
- The required frontend stack fully matches: React and/or Vue and/or Angular and/or TypeScript/JavaScript. Next.js, Nuxt, Redux, and the rest of those ecosystems count. Vanilla-JS frontend roles count.
- There is no hard/implied required backend in an unknown language. `company_uses` (another team's stack) does not count.

### B. Junior fullstack + Node.js → 9.0

- Junior / entry / working-student fullstack, or fullstack with **≤ 2 years** required (or no years stated). The title is not Senior / Lead / Staff / Principal.
- The backend is Node.js / TypeScript / JavaScript (Express, Nest, Fastify, Koa, and similar).

This still has to pass Step 1. A must-have of more than 1 year of backend fails the years gate and never reaches this category. "1–2 years" of backend still passes that gate.

### C. Frontend-leaning fullstack with no hard JD constraints → 9.0

All of the following must be true:

- The role is fullstack but frontend-leaning (**>50%** UI / frontend).
- The JD does **not** hard-require a number of years ("must have N years", "N+ years required").
- The JD does **not** hard-require a specific backend language (no "must know Java / Python / Go / PHP / …"). Backend is optional, unspecified, or only nice-to-have.
- The JD does **not** require German as a must-have job language.

If the JD hard-requires years, a specific backend language, or German, do **not** use C. Those jobs are ordinary matches or hard fails, not Special Matches.

### Leipzig

Only on top of A, B, or C. If the location or the JD clearly places the job in **Leipzig** (including "Leipzig, Germany", "04103", "Leipzig (hybrid)", Leipzig-Halle):

- Add **+0.5 to +1.0**, cap **10.0**
- Include `"Located in Leipzig"` in `special_match_reasons`

A perfect React frontend role in Leipzig should be **9.5–10**, not 8.

## Score bands

- **0–3**: A hard gate failed
- **4–6**: Weak ordinary match
- **7–8**: Good ordinary match. Stay here when A/B/C do not apply. Mixed AND-stack jobs with an unknown required backend are fails (0–3), not this band.
- **9–10**: Special Match only. Leipzig jobs sit at the top of this band.
