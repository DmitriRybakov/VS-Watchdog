# Architecture

Watchdog is one Python process. It serves the register pages and a small JSON API from the same
application. Deployment differs only by environment variables: SQLite on a laptop, PostgreSQL on
Render, PostgreSQL on Azure.

## Layers

```
Jinja2 templates + HTMX
        |
   web/ routes          cli.py
        \                /
          services/
        /      |       \
 sources/  screening/  llm/      storage/
        \      |       /            |
              core/              database
```

| Layer | Responsibility |
| --- | --- |
| `core/` | Settings, logging, shared types. Knows nothing about the rest of the application. |
| `sources/` | Fetch notices from TED and, later, Doffin, EIB, World Bank. Normalise to our shape. |
| `screening/` | Deterministic rules, the three axis scores, caps and bands. |
| `llm/` | The single door to any language model. `provider.py` only. Default provider `disabled`. |
| `storage/` | The database. Only `repository.py` opens a session or issues a query. |
| `services/` | Use cases: ingest, screen, review, list. The only layer that combines the others. |
| `web/`, `cli.py` | Delivery. Parse input, call one service function, render. No logic, no queries. |

## The import rule

- `core/` imports nothing internal.
- `sources/`, `screening/`, `storage/` and `llm/` import `core` only.
- `services/` may import all of them.
- `web/` and `cli.py` import `services` and `core` only.
- Never import sideways between `sources/` and `screening/`. A source must not know how screening
  works, and screening must not know where a notice came from.

The rule exists so that a change to the TED response shape cannot reach the scoring logic, and a
change to the policy cannot reach the fetcher.

## Import-time behaviour

Importing any Watchdog module must not open a database connection, make a network call or write a
file. Configuration is read by `core.settings.get_settings()`, logging is set up by
`core.logging.configure_logging()`, and both are called from `create_app()` or a CLI command.
`tests/unit/test_import_side_effects.py` enforces this.

Migrations are an explicit command (`make migrate`). They never run at startup.

## No build toolchain

Pages are server-rendered Jinja2. Interactivity is HTMX requesting HTML fragments from the same
FastAPI routes. `htmx.min.js` and the Pico CSS classless build are vendored into
`src/watchdog/web/static/`; nothing is fetched from a CDN at runtime. There is no Node, npm, React
or bundler, and there must never be one.

## State

The database holds everything durable, including raw source payloads. `data/` is a cache and may be
deleted at any time. Nothing is ever deleted from the database: a tender that vanished from the
source, a rejected notice and a superseded score all remain, archived and searchable.
