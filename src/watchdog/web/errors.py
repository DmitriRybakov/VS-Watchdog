"""What a browser is shown when the database cannot be reached.

Without this, Starlette answers an unhandled exception with the five words
``Internal Server Error`` as plain text - no traceback, which was worth checking
rather than assuming, but nothing a colleague can act on either.

The split is deliberate and is the rule everywhere else in Watchdog: the page
says what happened and what to do, the log says why. The reason text comes from
the driver and names the host, the port and sometimes the database user; none of
those belong on a page that anyone who finds the address can provoke.

Only the two connection-level failures are handled here. A query that fails for
its own reasons - a column too narrow, a constraint refused - is a fault in this
application, and telling a colleague to wait and try again would be wrong.
"""

from __future__ import annotations

from sqlalchemy.exc import InterfaceError, OperationalError
from starlette.requests import Request
from starlette.responses import Response

from watchdog.core.clock import utc_now
from watchdog.core.logging import get_logger
from watchdog.core.settings import get_settings
from watchdog.web.templating import templates

log = get_logger(__name__)

# Raised when the connection itself fails: no DNS, refused credentials, a
# timeout, a pooled socket the other end has forgotten.
DATABASE_UNREACHABLE = (OperationalError, InterfaceError)

RETRY_AFTER_SECONDS = 30


async def database_unavailable(request: Request, exc: Exception) -> Response:
    """A short page, and the diagnosis in the log where it can be acted on."""
    occurred_at = utc_now()

    log.error(
        "database_unavailable",
        path=request.url.path,
        # Which database, because the usual cause is the wrong one. Never the URL.
        database=get_settings().database_target,
        error=str(exc),
    )

    return templates.TemplateResponse(
        request,
        "database_unavailable.html",
        {
            "retry_path": request.url.path,
            "occurred_at": occurred_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
        },
        status_code=503,
        headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
    )
