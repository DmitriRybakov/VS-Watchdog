"""FastAPI application factory.

Importing this module must not open a database connection or make a network
call. ``create_app()`` only wires routes and static files.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from watchdog import __version__
from watchdog.core.logging import configure_logging
from watchdog.core.settings import get_settings
from watchdog.web.routes import health, pages
from watchdog.web.templating import STATIC_DIR


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=not settings.is_dev)

    app = FastAPI(title="Watchdog", version=__version__, docs_url="/api/docs")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(health.router)
    app.include_router(pages.router)
    return app


app = create_app()
