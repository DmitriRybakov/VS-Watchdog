"""The one page that proves Jinja2 templates and the vendored HTMX both load."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from watchdog import __version__
from watchdog.web.templating import templates

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {"version": __version__})


@router.get("/partials/ping", response_class=HTMLResponse)
def ping(request: Request) -> HTMLResponse:
    """Fragment swapped in by HTMX, so a failed vendored asset is visible."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    return templates.TemplateResponse(request, "partials/ping.html", {"checked_at": now})
