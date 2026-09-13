"""Signing in and signing out. The only pages reachable without a session.

Both handlers check their own CSRF token, because both are ordinary forms and the
token therefore arrives in the body rather than in a header - see the note in
``web/security.py``. The sign-in form uses a double submit: a random value is put
in a cookie and in the form at the same time, and the two must match. That value
belongs to the browser and is kept across fetches of this page, because a browser
fetches it more often than a colleague looks at it.

A refused sign-in answers with a redirect to a fresh sign-in page rather than
re-rendering one. Refreshing then reloads a page instead of resubmitting a
password, and the page that comes back is one the browser has the cookie for.

Nothing here says which half of a wrong sign-in was wrong. "Check the username
and password" tells a colleague what to do and tells a script nothing.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from watchdog.core.logging import get_logger
from watchdog.core.settings import get_settings
from watchdog.web import security
from watchdog.web.templating import templates

router = APIRouter(tags=["auth"])
log = get_logger(__name__)

throttle = security.SignInThrottle()

# Everything a refused sign-in is allowed to say. The redirect carries the key,
# never the sentence, so nothing a browser sends can be put onto the page.
REFUSALS = {
    "stale": "That page had gone stale. Try signing in again.",
    "origin": "That sign-in did not come from the Watchdog sign-in page. Try again.",
    "credentials": "Check the username and password, then try again.",
    "locked": "Too many wrong passwords from this address. Wait fifteen minutes and try again.",
}


@router.get(security.LOGIN_PATH, response_class=HTMLResponse)
def login_form(request: Request, next: str = "/", error: str = "") -> HTMLResponse:
    """The sign-in page. Styled inline, because the stylesheet needs a session."""
    nonce = security.form_csrf_nonce(request)
    response = templates.TemplateResponse(
        request,
        "login.html",
        {
            "next": security.safe_next(next),
            "csrf_token": nonce,
            "message": REFUSALS.get(error),
        },
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
    cookie_nonce = request.cookies.get(security.CSRF_COOKIE) or ""

    if not security.same_origin(request):
        log.warning("sign_in_refused", reason="foreign_origin")
        return _refused(destination, "origin")

    if not security.csrf_matches(cookie_nonce, csrf_token):
        log.warning(
            "sign_in_refused",
            reason="csrf_mismatch",
            csrf_cookie_present=bool(cookie_nonce),
            form_token_present=bool(csrf_token),
        )
        return _refused(destination, "stale")

    try:
        throttle.check(address)
    except security.SignInRefused:
        log.warning("sign_in_refused", reason="locked_out")
        return _refused(destination, "locked")

    if not security.check_password(settings, username, password):
        throttle.failed(address)
        log.warning("sign_in_refused", reason="wrong_credentials")
        return _refused(destination, "credentials")

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


def _refused(destination: str, error: str) -> RedirectResponse:
    """Back to the sign-in page as a redirect, carrying one sentence's worth of key.

    303 rather than a re-rendered page, so that refreshing reloads the form
    instead of sending the password again.
    """
    target = f"{security.LOGIN_PATH}?next={quote(destination, safe='')}&error={error}"
    return RedirectResponse(target, status_code=303)
