# Matching rules (layer 1)

The model reads the CV (`docs/user_profile.md`) and the job posting, then **classifies** the job.
It does not produce the final score: code turns these labels into a score with the weights in
`MATCH_WEIGHTS` (`src/core/config.py`). Company business / markets (layer 2) are looked up
separately.

## Hard fails — set `hard_fail` and quote the posting in `hard_fail_evidence`

1. **German required.** The posting states German as required / mandatory / fluent / a minimum
   level (B1, B2, C1, C2, "verhandlungssicher", "fließend"). German as a plus, nice to have,
   preferred, "von Vorteil", or an alternative to English ("German or English") is **not** a fail.
   Do not infer German from location, company nationality, or a German office.
2. **Non-JS backend required.** Report how the posting asks for backend languages in
   `backend_language_ask` (code decides the effect):
   - `required_non_js`: a backend language other than Node.js / TypeScript / JavaScript (Python,
     Java, Kotlin, Go, PHP, Ruby, C#/.NET, Scala, Rust, C++) with explicit hard wording: "X is
     required", "must have X", "strong X", "expert X", "X is our primary language", or a must-have
     list that names only non-JS backends ("Backend experience in Java / Spring Boot").
     Only this one is a hard fail.
   - `implied_non_js`: a non-JS language listed for the role without hard wording, including
     alongside TypeScript ("experience with Python, TypeScript and modern web technologies",
     "Java/Kotlin or Go and TypeScript"). A gap, not a fail.
   - `or_list_with_js`: Node.js / TypeScript is one accepted option ("Node.js or Python").
   - `nice_to_have`: "ideally X", "e.g. X", "a plus", willingness to learn.
   - `company_uses`: X appears only in a stack tour / "our backend is X" / another team.
   - `js_only` or `none`.
   A frontend-only role at a company with a non-JS backend is fine.
3. **Leadership or above senior.** Lead, Tech Lead, Team Lead, Staff, Principal, Architect,
   Head of, Engineering Manager, CTO/VP, Founding Engineer, co-founder, or the job centres on
   leading people / setting company-wide technical direction. Plain "Senior" is fine; mentoring
   juniors as a side duty is fine. If the title is not a lead title but the duties include leading
   a squad / team / projects / people ("lead a product squad", "lead projects", "people
   leadership"), set `seniority` to `lead_or_above` — she has never led a team.
4. **Not web product engineering.** Embedded, hardware, HMI/Qt/C++ desktop, networking / systems
   integration, DevOps/SRE/infrastructure, data engineering / data science, ML/AI model
   engineering (training, fine-tuning, ML platforms), native mobile only, QA-only, sales / solutions
   / support engineering, business-process automation / RPA, non-engineering roles.
5. **Backend-heavy.** Backend-only, labelled backend-focused, or needs several years of senior
   backend / distributed-systems ownership. The candidate has about 1 year of backend.
6. **Too many years.** A must-have of more than 7 years overall/frontend, or more than 2 years of
   backend specifically.
7. **Contract type or location.** Working student, internship, part-time, freelance; or not in
   Germany (remote roles that require living outside Germany).

If the description is missing, truncated, or clearly belongs to a different job, set
`description_usable` false instead of guessing. A recruiter / agency posting for another company
is still usable.

## Classification fields (no hard fail needed)

- `role_type`: frontend, fullstack_frontend_leaning, fullstack_balanced, fullstack_backend_leaning,
  backend, not_web_dev.
- `stack_fit`: strong (React / Vue / Angular / Nuxt / Next / TypeScript, and Node.js if backend is
  in scope), partial (JS/TS but an unusual framework or a meaningful non-JS part), weak.
- `seniority`: junior, mid, senior, lead_or_above. The candidate sees herself as senior frontend.
- `ai_product_work`: true when the role builds LLM / AI features into a product (not ML research).
- `english_team`: true / false / unclear from the posting.

`summary`: two short sentences on what the job is and why it does or does not fit.
`watch_outs`: one honest gap, or "none".
