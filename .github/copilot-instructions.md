# Project instructions for GitHub Copilot

These rules apply to every chat in this repository. Follow them unless I explicitly say otherwise in a message.

## What this project is

Watchdog finds public procurement opportunities relevant to Entr Advisory & Decision Support, a team at Aker Solutions that supports early-stage energy and decarbonisation projects before major commitments are locked in. It reads notices from open procurement APIs (TED first, later Doffin, EIB, World Bank), screens them against our mandate, scores them 1-5, and presents a register that a business development colleague reviews and decides on.

It is a decision-support tool for a small team. Being able to explain and defend a score matters more than clever code. Missing a relevant tender is far worse than showing an irrelevant one.

## The interface must work for a non-programmer

The daily user is a business development colleague with no programming knowledge. Everything routine — updating from TED, filtering, opening a tender, recording a decision, editing keywords, re-screening, exporting — must be doable from clearly labelled controls in the browser. If a task can only be done from a terminal, it is not finished.

Long operations show progress and then a result. Failures say what failed and what to do next, never a stack trace. Keep the main view dense and focused on the decision, with evidence and technical detail one click away rather than on screen by default.

## Architecture - do not change this

One Python process: Jinja2 templates + HTMX -> FastAPI routes -> services -> engine modules + database

- Import direction, between packages: `core/` imports no other package. `sources/`, `storage/` and `llm/` import `core` only. `screening/` imports `core` and `llm` — the assessment stage holds a provider, the dependency runs one way only, and `llm/` never imports `screening/`. `services/` may import all of them. `web/` and `cli.py` import `services` and `core` only. Never import sideways between `sources/` and `screening/`. Within a single package, modules may import each other freely.
- **Never add Node, npm, React or a bundler.** Pages are server-rendered Jinja2 templates in `web/templates/`, using HTMX for partial updates. HTMX and the CSS are vendored into `web/static/` and served from there — no CDN request at runtime.
- Application queries live only in `storage/repository.py` — no SQL, no ORM session and no query outside it. Engine and session setup belong in `storage/db.py`, table definitions in `storage/tables.py`, and schema changes in the Alembic migration files. Those three are the only exceptions.
- Route handlers parse input, call one service function, and render. No business logic and no queries in routes or templates.
- Every language-model call goes through the `LLMProvider` interface in `llm/provider.py`. A model SDK may be imported only inside its own provider implementation in `llm/` (for example `llm/azure_openai.py`) and nowhere else. The default provider is `disabled` and the entire application must work with it.
- Do not add libraries unless I ask. Current ones: fastapi, uvicorn, jinja2, httpx, pydantic, pydantic-settings, sqlalchemy, alembic, psycopg, typer, pyyaml, structlog, tenacity.
- Never install the PyPI package named `watchdog` (the filesystem watcher) into this venv, and tell me before adding any dependency that requires it — it collides with our own import package.

## The screening policy is authoritative

`docs/SCREENING_POLICY.md` holds the mandate, the three scoring axes, the caps and the bands. **Follow it exactly.** If your own judgement differs on what should score what, follow the document anyway and say so rather than silently changing the logic.

`docs/TED_API_CONTRACT.md` holds the endpoint, the request shape and the field mapping.

## Configuration is data, never code

Rules, mandate text, weights, thresholds and CPV lists live in `config/*.yaml` as the seed and in the `config_version` table as the active version. Never hardcode any of them, and never read a config file directly from business logic — read the active version through the service.

## Units and conventions - get these right

- All timestamps are stored, compared and logged in **UTC**, timezone-aware. Convert to local time only for display.
- An unknown fact is **null**. Never `""`, never `"Unknown"`, never `0`. A missing deadline and a deadline of midnight are different things.
- Money keeps its currency with it. Never assume EUR.
- Scores: the final score is an integer **1-5**; the three axis scores are **0-5 or None**. None means "not established" and must never be silently turned into 0.
- Source text is stored **exactly as received, in its original language**. Normalise case, accents and hyphens only inside the matching code, never in storage or display.
- Identity is (source, source_id). A title is never an identifier.

## Data provenance - never blur these four

Source fact (from the API) · document extract (from a notice or attachment) · Watchdog intelligence (rules or model) · human judgement (a colleague's review).

Every stored and displayed value belongs to exactly one of these. A machine result never overwrites a human review, and a generated value is never displayed as though it came from TED.

## Four things that are easy to get wrong here

1. **Keywords are evidence, not a verdict.** A tender with no keyword match is not irrelevant — TED titles arrive in every EU language and our vocabulary is English. The rules stage never rejects anything on its own.
2. **An acronym is not a domain.** HVAC usually means heating and ventilation, not high-voltage AC. Ambiguous aliases count only when their required context words appear nearby.
3. **Thin text is not evidence of irrelevance.** A two-line notice means we do not know, which routes to human review, never to archive.
4. **Nothing is ever deleted.** Not a tender that vanished from the source, not a rejected notice, not a superseded score. Archive means low priority and still searchable.

## How to work with me

- I am an engineer, not a programmer. Explain what you changed in plain language, briefly, and say why.
- Prefer clear, readable code over clever code. This tool has to be defensible to colleagues and to IT.
- Keep every intermediate value available and inspectable — the matched words, the evidence quotes, the version numbers. Hidden intermediates make a score impossible to defend.
- When you are unsure what I mean, ask rather than guessing.
- Stay focused on what I asked. If you notice an unrelated problem, mention it rather than fixing it silently.
- Tests never call the network or a real model; use recorded fixtures and the fake provider. Do not weaken, skip or delete a test to make a change pass.
- Secrets come only from environment variables. Never log a secret, a whole notice, or a model payload.
- The database is the only store for application data: tenders, raw payloads, screening results, reviews and user-edited settings. Locally that database is SQLite in `data/`; when hosted it is PostgreSQL, and nothing else about the code changes. Anything else under `data/` is a disposable cache that may be deleted at any moment, and no code may depend on a file surviving a restart. Exports are generated on demand and streamed to the user, never stored. Migrations run as an explicit command, never at startup.
- I develop on Windows. The Makefile is for Linux and containers; `tasks.ps1` is what I actually run. Any change to one must be mirrored in the other in the same commit.
- Never commit, push or deploy unless I explicitly ask.
- Before finishing a task, run the test, lint, format-check and type-check commands, then report every changed file and anything you could not verify.

## Review notes

After completing the requested change, add a short "Notes" section — but only if you have something substantive to raise. Use it to flag:

- Logic that will not do what the spec actually intends
- Choices that become expensive to reverse later (data model, API contracts, file structure)
- Approaches that work technically but are confusing for a non-technical end user
- Assumptions you had to make because the prompt was ambiguous
- Anything in the screening policy or the field mapping that looks transcribed wrongly, since those errors produce plausible-looking scores rather than visible failures
- Anything a test or a real API response showed that contradicts what I asked for — say what you saw

Rules:

- Raise, do not act. Do not refactor, rename or restructure beyond the request.
- Threshold: only raise what you would defend in a code review. If nothing meets that bar, write nothing at all.
- Maximum 3 items, 1-2 sentences each, plain language.
- State the consequence, not just the label: what breaks, and when.
