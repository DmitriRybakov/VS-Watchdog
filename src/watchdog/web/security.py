"""Sign-in for a shared pilot, and the cross-site check that has to come with it.

What this is and is not. It is **one username and one password for the team**,
held in environment variables, proving only that whoever is looking at the
register was told the password. It is not a user directory, there are no roles,
and nothing here identifies a person - the name on a review still comes from
``web/reviewer.py`` and is still only attribution.

Three decisions worth stating, because each of them is a trade made on purpose:

- **Everything is behind it except ``/health``.** Pages, fragments, exports, the
  API docs and the vendored CSS all need a session. A monitor needs one endpoint
  and that endpoint now says nothing but "the process is up", so there is nothing
  left worth leaving open. The sign-in page carries its own styling for exactly
  this reason.
- **The session is a signed cookie, not a table.** There is no server-side session
  store to keep, which means a free host that sleeps and wakes loses nothing. The
  cost is that a session cannot be revoked individually: changing
  ``SESSION_SECRET`` or the password ends all of them at once, which is the right
  granularity for one shared login.
- **The CSRF token is derived from the session cookie.** Every state-changing
  request must present it, so a form on somebody else's site cannot record a
  verdict or start a run with a colleague's cookie. HTMX sends it as a header for
  the whole page; the two sign-in forms are ordinary forms and carry it as a
  field, checked in the route where the form body is available. A POST that
  presents no token is refused rather than allowed through.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlsplit

from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from watchdog.core.clock import utc_now
from watchdog.core.logging import get_logger
from watchdog.core.settings import Settings

log = get_logger(__name__)

SESSION_COOKIE = "watchdog_session"
CSRF_COOKIE = "watchdog_csrf"
CSRF_HEADER = "X-CSRF-Token"
CSRF_FIELD = "csrf_token"

LOGIN_PATH = "/login"
LOGOUT_PATH = "/logout"

# The only route reachable without signing in. A monitor needs one; anything more
# is a page somebody can read without the password.
PUBLIC_PATHS = frozenset({"/health"})

# Reachable without a session, because they are how a session is obtained.
ANONYMOUS_PATHS = frozenset({LOGIN_PATH})

# Ordinary form posts, so the token arrives in the body rather than a header and
# the route checks it where the body has already been parsed. Every other unsafe
# request is checked here, before it reaches a route.
FORM_CSRF_PATHS = frozenset({LOGIN_PATH, LOGOUT_PATH})

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# How many times a wrong password may be tried from one address before that
# address is refused for a while. A shared password on the open internet is
# guessable at thousands of tries a second; this makes it thousands of hours.
MAX_FAILURES = 5
LOCKOUT = timedelta(minutes=15)


class SignInRefused(Exception):
    """Too many wrong passwords from one address. Shown as a plain sentence."""


@dataclass(frozen=True)
class SessionUser:
    """Who the cookie says is signed in, and when they signed in."""

    username: str
    issued_at: datetime


# ------------------------------------------------------------------- signing


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _digest(secret: str, payload: str) -> str:
    return _b64(hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest())


def _sign(secret: str, payload: str) -> str:
    return f"{payload}.{_digest(secret, payload)}"


def _unsign(secret: str, token: str) -> str | None:
    payload, _, signature = token.rpartition(".")
    if not payload or not signature:
        return None
    if not hmac.compare_digest(signature, _digest(secret, payload)):
        return None
    return payload


def issue_session(settings: Settings, username: str, *, now: datetime | None = None) -> str:
    """The cookie value for a successful sign-in. Carries its own issue time."""
    if not settings.session_secret:
        raise RuntimeError("a session cannot be issued without SESSION_SECRET")
    issued = int((now or utc_now()).timestamp())
    return _sign(settings.session_secret, f"v1.{_b64(username.encode('utf-8'))}.{issued}")


def read_session(settings: Settings, raw: str | None) -> SessionUser | None:
    """Who this cookie is for, or None if it is missing, tampered with or old.

    A cookie for a username that is no longer the configured one is refused, so
    changing ``AUTH_USERNAME`` ends the sessions issued under the old one.
    """
    if not raw or not settings.session_secret:
        return None

    payload = _unsign(settings.session_secret, raw)
    if payload is None:
        return None

    version, _, rest = payload.partition(".")
    encoded, _, issued_text = rest.partition(".")
    if version != "v1" or not encoded or not issued_text:
        return None

    try:
        username = _unb64(encoded).decode("utf-8")
        issued_at = datetime.fromtimestamp(int(issued_text), UTC)
    except (ValueError, UnicodeDecodeError, binascii.Error, OverflowError, OSError):
        return None

    if utc_now() - issued_at > timedelta(hours=settings.session_hours):
        return None
    if not settings.auth_username or username != settings.auth_username:
        return None

    return SessionUser(username=username, issued_at=issued_at)


def csrf_token(settings: Settings, session_cookie: str | None) -> str:
    """The token this session must present. Derived, so nothing has to be stored.

    It changes when the session changes, and it cannot be computed by anyone who
    cannot already read the cookie - which is the whole of the protection.
    """
    if not settings.session_secret or not session_cookie:
        return ""
    return _digest(settings.session_secret, f"csrf.{session_cookie}")


def csrf_matches(expected: str, supplied: str | None) -> bool:
    if not expected or not supplied:
        return False
    return hmac.compare_digest(expected, supplied)


# The shape ``secrets.token_urlsafe`` produces. A cookie of any other shape is
# treated as absent rather than echoed back into the page.
_NONCE = re.compile(r"\A[A-Za-z0-9_-]{16,64}\Z")


def form_csrf_nonce(request: Request) -> str:
    """The sign-in form's half of the double submit: the browser's own, or a new one.

    Reused rather than reminted, because the sign-in page is fetched more often
    than it is looked at. A browser asks for ``/favicon.ico`` beside every page,
    that ask carries no session, and it is redirected here and follows the
    redirect. Minting a nonce on each fetch replaces the cookie belonging to the
    form already on screen, and the sign-in then fails on a page nobody touched.
    """
    existing = request.cookies.get(CSRF_COOKIE) or ""
    return existing if _NONCE.match(existing) else secrets.token_urlsafe(24)


def same_origin(request: Request) -> bool:
    """Whether a form post came from this site, judged by host name alone.

    The scheme is deliberately not compared. Render terminates TLS at its own
    proxy and forwards plain HTTP, so a scheme rebuilt from the connection reads
    ``http`` while the browser says ``https``; comparing the two would refuse
    every sign-in on the hosted site. The ``Host`` header carries the public name
    in both places, so comparing that compares like with like.

    A request carrying no ``Origin`` is not refused here - not every client sends
    one - and the token in the body still has to match the cookie.
    """
    origin = request.headers.get("origin")
    if not origin or origin == "null":
        return True
    host = request.headers.get("host")
    if not host:
        return False
    return urlsplit(origin).netloc.casefold() == host.casefold()


# ------------------------------------------------------------------- cookies


def _cookie_kwargs(settings: Settings) -> dict[str, Any]:
    return {
        "httponly": True,
        # Lax rather than Strict: a link from an email to a notice should still
        # land on the notice rather than on a sign-in page.
        "samesite": "lax",
        # Only ever sent over TLS when hosted. Off on a laptop, where there is none.
        "secure": settings.is_hosted,
        "path": "/",
    }


def set_session_cookie(response: Response, settings: Settings, value: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, value, max_age=settings.session_hours * 3600, **_cookie_kwargs(settings)
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(SESSION_COOKIE, **_cookie_kwargs(settings))


def set_form_csrf_cookie(response: Response, settings: Settings, nonce: str) -> None:
    """The other half of the double submit used by the sign-in form."""
    response.set_cookie(CSRF_COOKIE, nonce, max_age=3600, **_cookie_kwargs(settings))


# ------------------------------------------------------------------ throttle


class SignInThrottle:
    """Wrong passwords, counted per address. In memory, which is enough for one worker.

    It is deliberately not in the database: a lockout that survives a restart
    would also survive a colleague's typo for fifteen minutes after a deploy, and
    the point is to slow a script down, not to lock a team out.
    """

    def __init__(self, *, limit: int = MAX_FAILURES, lockout: timedelta = LOCKOUT) -> None:
        self._limit = limit
        self._lockout = lockout
        self._failures: dict[str, tuple[int, datetime]] = {}

    def check(self, address: str) -> None:
        count, last = self._failures.get(address, (0, utc_now()))
        if count >= self._limit and utc_now() - last < self._lockout:
            raise SignInRefused(
                "Too many wrong passwords from this address. Wait fifteen minutes and try again."
            )

    def failed(self, address: str) -> None:
        count, last = self._failures.get(address, (0, utc_now()))
        if utc_now() - last >= self._lockout:
            count = 0
        self._failures[address] = (count + 1, utc_now())

    def succeeded(self, address: str) -> None:
        self._failures.pop(address, None)


def client_address(request: Request) -> str:
    """Who is asking, as far as the host will say. Used only for counting failures."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_password(settings: Settings, username: str, password: str) -> bool:
    """Both values compared in constant time, so neither can be found a letter at a time."""
    if not settings.auth_username or not settings.auth_password:
        return False
    name_ok = hmac.compare_digest(username.strip(), settings.auth_username)
    password_ok = hmac.compare_digest(password, settings.auth_password)
    return name_ok and password_ok


def safe_next(target: str | None) -> str:
    """Where to go after signing in. Anything not a path on this site becomes "/".

    A ``next`` parameter that can name another site turns the sign-in page into a
    redirector somebody else can point at their own.
    """
    if not target or not target.startswith("/") or target.startswith("//"):
        return "/"
    return target


# ---------------------------------------------------------------- middleware


class AuthMiddleware:
    """Refuses anything without a valid session, and anything without a CSRF token.

    Plain ASGI rather than ``BaseHTTPMiddleware``: it only ever reads the request
    headers and either passes the call straight through or answers it itself. It
    never touches the response, so a streamed export and a background run behave
    exactly as they do without it.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        raw_cookie = request.cookies.get(SESSION_COOKIE)
        session = read_session(self.settings, raw_cookie)

        if session is None and path not in ANONYMOUS_PATHS:
            await self._refuse(request, send)
            return

        token = csrf_token(self.settings, raw_cookie)
        state = scope.setdefault("state", {})
        state["csrf_token"] = token
        state["user"] = session.username if session is not None else None

        method = scope.get("method", "GET")
        unsafe = method not in SAFE_METHODS and path not in FORM_CSRF_PATHS
        if unsafe and not csrf_matches(token, request.headers.get(CSRF_HEADER)):
            log.warning("csrf_rejected", path=path, method=method)
            await PlainTextResponse(
                "This request could not be confirmed as coming from Watchdog. "
                "Reload the page and try again.",
                status_code=403,
            )(scope, receive, send)
            return

        await self.app(scope, receive, send)

    async def _refuse(self, request: Request, send: Send) -> None:
        """No session. Send the browser to the sign-in page, whichever way it asked."""
        wanted = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        target = f"{LOGIN_PATH}?next={quote(wanted, safe='')}"

        if request.headers.get("HX-Request") == "true":
            # A fragment swap must not put a whole sign-in page inside the table.
            response: Response = PlainTextResponse(
                "Your session has ended. Signing in again.",
                status_code=401,
                headers={"HX-Redirect": LOGIN_PATH},
            )
        else:
            response = RedirectResponse(target, status_code=303)

        await response(request.scope, request.receive, send)
