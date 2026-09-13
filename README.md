# Watchdog

Tender screening service for the Entr Advisory & Decision Support team at Aker Solutions.

Watchdog pulls public procurement notices from open APIs (TED first, later Doffin, EIB, World Bank),
screens them against our advisory mandate with deterministic rules plus an optional language model,
stores everything in a database, and serves a server-rendered register to our own team.

One Python process serves both the pages and a small JSON API. It runs on a laptop now, on Render
next, on Azure later - the only difference between those is environment variables.

No Node, no React, no bundler. Jinja2 templates with HTMX, both vendored into `web/static/`.

## Requirements

- Python 3.12 or newer
- Nothing else. No build toolchain.

## Setup (Windows PowerShell)

These commands never activate the virtual environment; they call its interpreter directly, so they
work the same in a terminal, a script and a task runner.

```powershell
cd C:\dev\VS-Watchdog
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Or run `.\tasks.ps1 install`, which does the same three steps. See [Task runner](#task-runner).

## Running

```powershell
.\.venv\Scripts\python.exe -m uvicorn watchdog.web.app:app --reload --port 8000
```

Then open <http://127.0.0.1:8000/> for the page and <http://127.0.0.1:8000/health> for the JSON
health endpoint.

## Checks

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pytest
```

## Database migrations

Migrations are an explicit command. They never run at startup, so starting the application can never
change the shape of the database.

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Run this once after cloning, and again after pulling a change that adds a migration. It creates the
tables - `tender`, `tender_change`, `screening_result`, `review`, `tender_detail`, `run` and
`watermark` - in whatever database `DATABASE_URL` points at: the SQLite file under `data/` on a
laptop, PostgreSQL when hosted. Nothing is ever deleted from those tables; superseded results and
notices that vanished from the source stay, and stay searchable.

## Task runner

The same six targets exist twice: `tasks.ps1` for Windows, where `make` is not installed by default,
and the `Makefile` for Linux, macOS, CI and containers. They run the same commands, and both call
the interpreter inside `.venv` directly, so the virtual environment never needs to be activated.

| Windows | Linux / macOS / CI | Does |
| --- | --- | --- |
| `.\tasks.ps1 install` | `make install` | Create the venv and install the package with dev extras |
| `.\tasks.ps1 lint` | `make lint` | `ruff check` and `ruff format --check` |
| `.\tasks.ps1 typecheck` | `make typecheck` | `mypy` |
| `.\tasks.ps1 test` | `make test` | `pytest` with coverage |
| `.\tasks.ps1 run` | `make run` | `uvicorn` with reload on port 8000 |
| `.\tasks.ps1 migrate` | `make migrate` | `alembic upgrade head` |

Run `.\tasks.ps1` with no argument to print the list. `test`, `run` and `migrate` pass any extra
arguments through, so `.\tasks.ps1 test tests/unit -x` works.

If PowerShell refuses to run the script, allow local scripts once per user:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

## Configuration

Everything comes from environment variables; see `.env.example`. Defaults are chosen so that a fresh
clone runs with no configuration at all: SQLite in `data/`, and the language model provider
`disabled`.

## Hosted deployment

A laptop needs no sign-in and no PostgreSQL. Anything hosted needs both, and refuses to start
without them rather than coming up open to the internet or writing to a disk that the next deploy
erases. One shared username and password covers the whole site; `/health` is the only route in front
of it, and says nothing but that the process is answering.

See [docs/DEPLOY_RENDER.md](docs/DEPLOY_RENDER.md) for the Render and Supabase setup, the exact
Windows commands for migrating and filling a hosted database, and the restart test.

## Layout

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the layers and the import rule.
