"""Signing in and signing out. The only pages reachable without a session.

Both handlers check their own CSRF token, because both are ordinary forms and the
token therefore arrives in the body rather than in a header - see the note in
``web/security.py``. The sign-in form uses a double submit: a random value is put
in a cookie and in the form at the same time, and the two must match.

Nothing here says which half of a wrong sign-in was wrong. "Check the username
and password" tells a colleague what to do and tells a script nothing.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from watchdog.core.logging import get_logger
from watchdog.core.settings import get_settings
from watchdog.web import security
from watchdog.web.templating import templates

router = APIRouter(tags=["auth"])
log = get_logger(__name__)

throttle = security.SignInThrottle()


@router.get(security.LOGIN_PATH, response_class=HTMLResponse)
def login_form(request: Request, next: str = "/") -> HTMLResponse:
    """The sign-in page. Styled inline, because the stylesheet needs a session."""
    nonce = secrets.token_urlsafe(24)
    response = templates.TemplateResponse(
        request,
        "login.html",
        {"next": security.safe_next(next), "csrf_token": nonce, "message": None},
    )
    security.set_form_csrf_cookie(response, get_settings(), nonce)
    return response


@router.post(security.LOGIN_PATH, response_class=HTMLResponse)
def sign_in(
    request: Request,
    username: str = Form(default=""),
    password: str = Form(default=""),
    csrf_token: str = Form(default=""),
    next: str = Form(default="/"),
) -> Response:
    settings = get_settings()
    destination = security.safe_next(next)
    address = security.client_address(request)

    if not security.csrf_matches(request.cookies.get(security.CSRF_COOKIE) or "", csrf_token):
        return _refused(request, destination, "That page had gone stale. Try signing in again.")

    try:
        throttle.check(address)
    except security.SignInRefused as exc:
        log.warning("sign_in_locked_out")
        return _refused(request, destination, str(exc))

    if not security.check_password(settings, username, password):
        throttle.failed(address)
        log.warning("sign_in_failed")
        return _refused(request, destination, "Check the username and password, then try again.")

    throttle.succeeded(address)
    log.info("sign_in_succeeded")

    response = RedirectResponse(destination, status_code=303)
    security.set_session_cookie(response, settings, security.issue_session(settings, username))
    return response


@router.post(security.LOGOUT_PATH)
def sign_out(request: Request, csrf_token: str = Form(default="")) -> Response:
    settings = get_settings()
    expected = security.csrf_token(settings, request.cookies.get(security.SESSION_COOKIE))

    if not security.csrf_matches(expected, csrf_token):
        return PlainTextResponse(
            "That sign-out could not be confirmed as coming from Watchdog. "
            "Reload the page and try again.",
            status_code=403,
        )

    response = RedirectResponse(security.LOGIN_PATH, status_code=303)
    security.clear_session_cookie(response, settings)
    return response


def _refused(request: Request, destination: str, message: str) -> HTMLResponse:
    """The sign-in page again, with one sentence saying what to do, and a fresh token."""
    nonce = secrets.token_urlsafe(24)
    response = templates.TemplateResponse(
        request,
        "login.html",
        {"next": destination, "csrf_token": nonce, "message": message},
        status_code=401,
    )
    security.set_form_csrf_cookie(response, get_settings(), nonce)
    return response
