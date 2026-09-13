"""Sign-in, and the cross-site check that goes with it.

What these prove, in order: nothing but /health answers without a session; a
session is obtained only with the shared password; and a request that changes
something is refused unless it carries this session's own token.

Nothing here calls TED or a model. The one POST used to test the token is the
reviewer name, which touches neither.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from freezegun import freeze_time

from watchdog.core.clock import utc_now
from watchdog.core.settings import get_settings
from watchdog.storage.repository import Repository
from watchdog.web import security

USERNAME = "entr"
PASSWORD = "a-password-nobody-would-guess"
SECRET = "x" * 48


@pytest.fixture
def signed_in_app(monkeypatch: pytest.MonkeyPatch, repository: Repository) -> Iterator[TestClient]:
    """The application with the shared sign-in switched on, over an empty database."""
    monkeypatch.setenv("AUTH_USERNAME", USERNAME)
    monkeypatch.setenv("AUTH_PASSWORD", PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    get_settings.cache_clear()

    from watchdog.web.app import create_app
    from watchdog.web.deps import get_repository

    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repository

    try:
        with TestClient(app) as client:
            yield client
    finally:
        get_settings.cache_clear()


def sign_in(client: TestClient) -> None:
    """Do what a colleague does: open the page, type the password, submit."""
    client.get("/login")
    response = client.post(
        "/login",
        data={
            "username": USERNAME,
            "password": PASSWORD,
            "csrf_token": client.cookies[security.CSRF_COOKIE],
            "next": "/register",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def token(client: TestClient) -> str:
    return security.csrf_token(get_settings(), client.cookies[security.SESSION_COOKIE])


# ------------------------------------------------------------- what is public


def test_health_is_the_only_thing_that_answers_without_signing_in(
    signed_in_app: TestClient,
) -> None:
    response = signed_in_app.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "watchdog"}


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/register",
        "/register/results",
        "/register/export.csv",
        "/register/export.xlsx",
        "/runs",
        "/runs/status",
        "/api/docs",
        "/openapi.json",
        "/static/css/watchdog.css",
    ],
)
def test_every_other_route_sends_a_stranger_to_the_sign_in_page(
    signed_in_app: TestClient, path: str
) -> None:
    response = signed_in_app.get(path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?next=")


def test_where_you_were_going_survives_the_sign_in_page(signed_in_app: TestClient) -> None:
    response = signed_in_app.get("/register?band=review&q=hydrogen", follow_redirects=False)

    assert "next=%2Fregister%3Fband%3Dreview%26q%3Dhydrogen" in response.headers["location"]


def test_a_fragment_request_is_told_to_navigate_rather_than_swapping_a_login_page(
    signed_in_app: TestClient,
) -> None:
    """A whole sign-in page swapped into a table cell would be unreadable."""
    response = signed_in_app.get("/runs/status", headers={"HX-Request": "true"})

    assert response.status_code == 401
    assert response.headers["HX-Redirect"] == "/login"


# --------------------------------------------------------------- signing in


def test_the_shared_password_gets_in(signed_in_app: TestClient) -> None:
    sign_in(signed_in_app)

    assert signed_in_app.get("/register").status_code == 200


def test_a_wrong_password_does_not(signed_in_app: TestClient) -> None:
    signed_in_app.get("/login")
    response = signed_in_app.post(
        "/login",
        data={
            "username": USERNAME,
            "password": "not the password",
            "csrf_token": signed_in_app.cookies[security.CSRF_COOKIE],
        },
        follow_redirects=False,
    )

    # A redirect to a fresh sign-in page, so refreshing reloads a form rather
    # than sending the password again.
    assert response.status_code == 303
    assert "error=credentials" in response.headers["location"]
    assert security.SESSION_COOKIE not in signed_in_app.cookies

    landed = signed_in_app.get(response.headers["location"])

    # Which half was wrong is not said, because saying it only helps a script.
    assert "username and password" in landed.text


def test_the_sign_in_page_does_not_need_the_stylesheet_it_cannot_load(
    signed_in_app: TestClient,
) -> None:
    html = signed_in_app.get("/login").text

    assert "<style>" in html
    assert "watchdog.css" not in html


def test_a_sign_in_form_from_somewhere_else_is_refused(signed_in_app: TestClient) -> None:
    signed_in_app.get("/login")

    response = signed_in_app.post(
        "/login",
        data={"username": USERNAME, "password": PASSWORD, "csrf_token": "made up"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=stale" in response.headers["location"]
    assert security.SESSION_COOKIE not in signed_in_app.cookies


def test_the_token_a_browser_already_holds_survives_another_fetch_of_the_page(
    signed_in_app: TestClient,
) -> None:
    """A browser fetches /login more often than a colleague looks at it.

    /favicon.ico has no session, is redirected here and follows the redirect. If
    that fetch reminted the nonce it would replace the cookie belonging to the
    form on screen, and the sign-in would be refused as stale. See
    tests/integration/test_sign_in_in_a_browser.py for the same thing in a browser.
    """
    signed_in_app.get("/login")
    first = signed_in_app.cookies[security.CSRF_COOKIE]

    signed_in_app.get("/login?next=%2Ffavicon.ico")

    assert signed_in_app.cookies[security.CSRF_COOKIE] == first


def test_the_session_cookie_cannot_be_read_by_a_script_or_sent_in_the_clear(
    signed_in_app: TestClient,
) -> None:
    signed_in_app.get("/login")
    response = signed_in_app.post(
        "/login",
        data={
            "username": USERNAME,
            "password": PASSWORD,
            "csrf_token": signed_in_app.cookies[security.CSRF_COOKIE],
        },
        follow_redirects=False,
    )

    header = next(
        value
        for value in response.headers.get_list("set-cookie")
        if value.startswith(security.SESSION_COOKIE)
    )

    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    # No Secure flag in a test, because a test is not hosted and has no TLS. What
    # sets it is Settings.is_hosted, which is what ENVIRONMENT=production turns on.
    assert "Secure" not in header


def test_an_edited_cookie_is_not_a_session(signed_in_app: TestClient) -> None:
    sign_in(signed_in_app)
    good = signed_in_app.cookies[security.SESSION_COOKIE]
    signed_in_app.cookies.set(security.SESSION_COOKIE, good[:-4] + "aaaa")

    assert signed_in_app.get("/register", follow_redirects=False).status_code == 303


def test_a_session_expires(signed_in_app: TestClient) -> None:
    sign_in(signed_in_app)
    hours = get_settings().session_hours

    with freeze_time(utc_now() + timedelta(hours=hours + 1)):
        assert signed_in_app.get("/register", follow_redirects=False).status_code == 303


def test_signing_out_ends_the_session(signed_in_app: TestClient) -> None:
    sign_in(signed_in_app)

    response = signed_in_app.post(
        "/logout", data={"csrf_token": token(signed_in_app)}, follow_redirects=False
    )

    assert response.status_code == 303
    assert signed_in_app.get("/register", follow_redirects=False).status_code == 303


# --------------------------------------------------------------------- csrf


def test_a_post_without_this_session_token_is_refused(signed_in_app: TestClient) -> None:
    sign_in(signed_in_app)

    response = signed_in_app.post("/register/reviewer", data={"name": "A Colleague"})

    assert response.status_code == 403
    assert "could not be confirmed" in response.text


def test_a_post_with_somebody_elses_token_is_refused(signed_in_app: TestClient) -> None:
    sign_in(signed_in_app)

    response = signed_in_app.post(
        "/register/reviewer",
        data={"name": "A Colleague"},
        headers={security.CSRF_HEADER: security.csrf_token(get_settings(), "another session")},
    )

    assert response.status_code == 403


def test_a_post_with_this_session_token_goes_through(signed_in_app: TestClient) -> None:
    sign_in(signed_in_app)

    response = signed_in_app.post(
        "/register/reviewer",
        data={"name": "A Colleague"},
        headers={security.CSRF_HEADER: token(signed_in_app)},
    )

    assert response.status_code == 200


def test_the_page_carries_the_token_for_every_htmx_request(signed_in_app: TestClient) -> None:
    sign_in(signed_in_app)

    html = signed_in_app.get("/register").text

    assert f'"X-CSRF-Token": "{token(signed_in_app)}"' in html


def test_repeated_wrong_passwords_are_refused_rather_than_answered(
    signed_in_app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from watchdog.web.routes import auth

    monkeypatch.setattr(auth, "throttle", security.SignInThrottle(limit=2))

    for _ in range(3):
        signed_in_app.get("/login")
        response = signed_in_app.post(
            "/login",
            data={
                "username": USERNAME,
                "password": "wrong",
                "csrf_token": signed_in_app.cookies[security.CSRF_COOKIE],
            },
        )

    assert "Too many wrong passwords" in response.text


def test_a_laptop_with_no_auth_configured_is_left_alone(client: TestClient) -> None:
    """The local setup is unchanged: no sign-in, no token, nothing to configure."""
    assert client.get("/register").status_code == 200
    assert client.post("/register/reviewer", data={"name": "A Colleague"}).status_code == 200


def test_a_hosted_process_refuses_to_start_without_a_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Open access is not a fallback. There is no configuration that produces it."""
    from watchdog.web.app import UnsafeToStart, create_app

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host/db")
    for name in ("AUTH_USERNAME", "AUTH_PASSWORD", "SESSION_SECRET"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()

    try:
        with pytest.raises(UnsafeToStart) as refused:
            create_app()
    finally:
        get_settings.cache_clear()

    assert "open to anyone" in str(refused.value)
    assert "SESSION_SECRET" in str(refused.value)


def test_a_hosted_process_refuses_to_start_on_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hosted disk is wiped at every deploy, so a file there loses every review."""
    from watchdog.web.app import UnsafeToStart, create_app

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///data/watchdog.db")
    monkeypatch.setenv("AUTH_USERNAME", USERNAME)
    monkeypatch.setenv("AUTH_PASSWORD", PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    get_settings.cache_clear()

    try:
        with pytest.raises(UnsafeToStart) as refused:
            create_app()
    finally:
        get_settings.cache_clear()

    assert "PostgreSQL" in str(refused.value)
