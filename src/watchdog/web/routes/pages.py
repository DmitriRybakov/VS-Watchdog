"""The one fragment that proves the vendored HTMX is loading.

The register took over ``/``. What is left here is the connectivity check: a
fragment HTMX swaps in, so a static file that failed to load shows on the page
rather than only in a browser console nobody opens.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from watchdog.web.templating import templates

router = APIRouter(tags=["pages"])


@router.get("/partials/ping", response_class=HTMLResponse)
def ping(request: Request) -> HTMLResponse:
    """Fragment swapped in by HTMX, so a failed vendored asset is visible."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    return templates.TemplateResponse(request, "partials/ping.html", {"checked_at": now})
