"""FastAPI application factory.

Importing this module must not open a database connection or make a network
call. ``create_app()`` only wires routes and static files.

Two things happen here that do not happen anywhere else:

- **A hosted process refuses to start when it is not safe to.** No sign-in and no
  PostgreSQL URL are both failures that look like success: the site comes up, and
  either anybody can read it or every review is lost at the next deploy. Refusing
  to start is the only version of those that announces itself.
- **The lifespan handler touches the database once**, to recover a run left behind
  by a process that died. It is tolerant of a database that has not been migrated
  yet: the register has a page that says so, and refusing to start would only
  hide it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from watchdog import __version__
from watchdog.core.logging import configure_logging, get_logger
from watchdog.core.settings import Settings, get_settings
from watchdog.web.routes import auth, health, pages, register, runs
from watchdog.web.security import AuthMiddleware
from watchdog.web.templating import STATIC_DIR

log = get_logger(__name__)


class UnsafeToStart(RuntimeError):
    """The process is configured in a way that would fail quietly. It stops instead."""


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Clear stranded runs once, then serve.

    A run that has stopped reporting progress is one whose process is gone - on a
    host that sleeps, that is a spin-down rather than a crash. Left alone it would
    hold the lock and keep the buttons disabled for good, with nothing on the page
    to press. A run that *is* still reporting belongs to another instance and is
    left exactly where it is.
    """
    from watchdog.services import jobs

    try:
        jobs.recover()
    except Exception as exc:  # noqa: BLE001 - a broken database must not stop the page loading
        log.warning("startup_recovery_skipped", error=str(exc))

    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=not settings.is_dev)
    _refuse_if_unsafe(settings)

    app = FastAPI(
        title="Watchdog",
        version=__version__,
        docs_url="/api/docs",
        lifespan=lifespan,
    )

    if settings.auth_required:
        # Added before the routes are mounted, so it covers the static files, the
        # API docs and every path that does not exist as well.
        app.add_middleware(AuthMiddleware, settings=settings)
        app.include_router(auth.router)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(health.router)
    app.include_router(register.router)
    app.include_router(runs.router)
    app.include_router(pages.router)
    return app


def _refuse_if_unsafe(settings: Settings) -> None:
    problems = settings.startup_problems()
    if not problems:
        return

    lines = "\n".join(f"  - {problem}" for problem in problems)
    log.error("startup_refused", problems=problems)
    raise UnsafeToStart(
        "Watchdog will not start with this configuration:\n"
        f"{lines}\n"
        "Set these environment variables on the service and deploy again. "
        "See docs/DEPLOY_RENDER.md."
    )


app = create_app()
