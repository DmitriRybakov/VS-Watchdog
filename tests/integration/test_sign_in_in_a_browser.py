"""Signing in from a browser, on the hosted configuration, behind Render's proxy.

Everything else about sign-in is tested through ``TestClient``, which calls the
application in process. That is the right tool for almost all of it and the wrong
tool for this: it never runs uvicorn's proxy-header handling, it never applies a
browser's rules about ``Secure`` cookies, and it only ever fetches the page a test
asks for. A browser does none of those three things.

The failure this file exists to catch was invisible for exactly that reason. A
browser asks for ``/favicon.ico`` beside every page it loads. That ask carries no
session, so it was redirected to ``/login``, and the browser followed the
redirect. The sign-in page minted a new nonce on every fetch, so the favicon's
fetch replaced the cookie belonging to the form already on screen, and the next
sign-in was refused with "That page had gone stale" on a page nobody had touched.

So this runs the real server, with ``forwarded_allow_ips`` set the way the Render
start command sets it, sends the headers Render's proxy sends, and keeps cookies
in a jar that judges them against the public ``https`` address rather than the
loopback one the test actually connects to. Nothing here relaxes a check: the
CSRF token, the origin check and the password are all the production ones.
"""

from __future__ import annotations

import email.message
import http.cookiejar
import re
import socket
import threading
import time
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest
import uvicorn

from watchdog.core.settings import get_settings
from watchdog.storage.repository import Repository
from watchdog.web import security

USERNAME = "entr"
PASSWORD = "a-password-nobody-would-guess"
SECRET = "s" * 48

# What the browser thinks it is talking to. Render terminates TLS here and
# forwards plain HTTP to the process, so this address never appears on the wire.
HOST = "watchdog.onrender.com"
PUBLIC = f"https://{HOST}"

ANOTHER_SITE = "https://tender-offers.example"


# --------------------------------------------------------------- the server


@dataclass
class Serving:
    base_url: str


@pytest.fixture
def hosted(monkeypatch: pytest.MonkeyPatch, repository: Repository) -> Iterator[Serving]:
    """The application as deployed: hosted mode, real uvicorn, proxy headers trusted."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@db.example/watchdog")
    monkeypatch.setenv("AUTH_USERNAME", USERNAME)
    monkeypatch.setenv("AUTH_PASSWORD", PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    get_settings.cache_clear()

    from watchdog.services import jobs
    from watchdog.web.app import create_app
    from watchdog.web.deps import get_repository
    from watchdog.web.routes import auth

    # The URL above is never connected to: the routes take the in-memory
    # repository below, and the one thing that would reach for the configured
    # database is startup recovery, which has nothing to recover here.
    monkeypatch.setattr(jobs, "recover", lambda: None)
    # Failures are counted per address in a module-level object. A fresh one per
    # test keeps one test's wrong passwords out of another's.
    monkeypatch.setattr(auth, "throttle", security.SignInThrottle())

    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repository

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(64)
    port = listener.getsockname()[1]

    server = uvicorn.Server(
        uvicorn.Config(app, log_level="warning", forwarded_allow_ips="*", proxy_headers=True)
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()

    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline:  # pragma: no cover - only on a broken start
            server.should_exit = True
            raise AssertionError("the server did not start")
        time.sleep(0.02)

    try:
        yield Serving(base_url=f"http://127.0.0.1:{port}")
    finally:
        server.should_exit = True
        thread.join(timeout=20)
        get_settings.cache_clear()


# --------------------------------------------------------------- the browser


def _as_message(headers: httpx.Headers) -> email.message.Message:
    message = email.message.Message()
    for key, value in headers.multi_items():
        message[key] = value
    return message


class _Received:
    """What ``http.cookiejar`` expects a response to look like."""

    def __init__(self, headers: httpx.Headers) -> None:
        self._message = _as_message(headers)

    def info(self) -> email.message.Message:
        return self._message


class Browser:
    """A cookie jar with browser rules, and nothing else a browser does not do.

    Cookies are chosen and stored against the public ``https`` address, so a
    ``Secure`` cookie behaves here exactly as it does on the hosted site.
    """

    def __init__(self, serving: Serving) -> None:
        self._base = serving.base_url
        self.jar = http.cookiejar.CookieJar()

    def fetch(
        self,
        path: str,
        *,
        data: dict[str, str] | None = None,
        origin: str | None = None,
        send_cookies: bool = True,
    ) -> httpx.Response:
        """One hop, with the headers Render's proxy puts on it."""
        policy = urllib.request.Request(PUBLIC + path)
        self.jar.add_cookie_header(policy)

        headers = {
            "Host": HOST,
            "X-Forwarded-Proto": "https",
            "X-Forwarded-For": "203.0.113.7",
            "Origin": origin or PUBLIC,
        }
        cookie = policy.get_header("Cookie")
        if cookie and send_cookies:
            headers["Cookie"] = cookie

        response = httpx.request(
            "POST" if data is not None else "GET",
            self._base + path,
            headers=headers,
            data=data,
            follow_redirects=False,
        )
        self.jar.extract_cookies(_Received(response.headers), policy)
        return response

    def navigate(self, path: str, **kwargs: object) -> httpx.Response:
        """A hop and then its redirects, which is what a browser actually does."""
        response = self.fetch(path, **kwargs)  # type: ignore[arg-type]
        for _ in range(5):
            if response.status_code not in (301, 302, 303, 307, 308):
                break
            response = self.fetch(response.headers["location"])
        return response

    def cookie(self, name: str) -> str | None:
        return next((c.value for c in self.jar if c.name == name), None)


def rendered_token(html: str) -> str:
    """The token in the form the colleague is actually looking at."""
    match = re.search(r'name="csrf_token" value="([^"]*)"', html)
    return match.group(1) if match else ""


def credentials(token: str, *, password: str = PASSWORD) -> dict[str, str]:
    return {
        "username": USERNAME,
        "password": password,
        "csrf_token": token,
        "next": "/register",
    }


# ------------------------------------------------------------ what must work


def test_a_browser_signs_in_and_reaches_a_protected_page(hosted: Serving) -> None:
    """The whole path, from an empty cookie jar to a page that needs a session."""
    browser = Browser(hosted)

    page = browser.navigate("/login?next=%2Fregister")
    assert page.status_code == 200

    posted = browser.fetch("/login", data=credentials(rendered_token(page.text)))

    assert posted.status_code == 303
    assert posted.headers["location"] == "/register"
    assert browser.cookie(security.SESSION_COOKIE) is not None

    protected = browser.navigate("/register")

    assert protected.status_code == 200


def test_the_favicon_a_browser_asks_for_does_not_stale_the_open_form(hosted: Serving) -> None:
    """The regression. A browser fetches /favicon.ico beside the page it loads.

    That fetch has no session, so it is redirected to the sign-in page and follows
    the redirect. The form on screen must still work afterwards.
    """
    browser = Browser(hosted)

    page = browser.navigate("/login?next=%2Fregister")
    token = rendered_token(page.text)

    icon = browser.navigate("/favicon.ico")
    assert icon.status_code == 200  # the sign-in page, followed from the redirect

    assert browser.cookie(security.CSRF_COOKIE) == token

    posted = browser.fetch("/login", data=credentials(token))

    assert posted.status_code == 303
    assert browser.navigate("/register").status_code == 200


def test_the_cookies_a_browser_is_given_are_secure(hosted: Serving) -> None:
    """Set over TLS only, unreadable by a script, and not sent cross-site."""
    browser = Browser(hosted)
    page = browser.navigate("/login")
    posted = browser.fetch("/login", data=credentials(rendered_token(page.text)))

    issued = page.headers.get_list("set-cookie") + posted.headers.get_list("set-cookie")

    assert issued
    for header in issued:
        assert "Secure" in header
        assert "HttpOnly" in header
        assert "SameSite=lax" in header


# --------------------------------------------------------- what must not work


def test_a_made_up_token_cannot_sign_in(hosted: Serving) -> None:
    browser = Browser(hosted)
    browser.navigate("/login")

    posted = browser.fetch("/login", data=credentials("a token nobody issued"))

    assert posted.status_code == 303
    assert "error=stale" in posted.headers["location"]
    assert browser.cookie(security.SESSION_COOKIE) is None
    assert browser.navigate("/register").status_code == 200  # the sign-in page again


def test_a_post_carrying_no_cookie_cannot_sign_in(hosted: Serving) -> None:
    """What a cross-site POST looks like on the wire: SameSite keeps the cookie at home."""
    browser = Browser(hosted)
    page = browser.navigate("/login")

    posted = browser.fetch(
        "/login", data=credentials(rendered_token(page.text)), send_cookies=False
    )

    assert posted.status_code == 303
    assert "error=stale" in posted.headers["location"]
    assert browser.cookie(security.SESSION_COOKIE) is None


def test_a_form_on_another_site_cannot_sign_in(hosted: Serving) -> None:
    """Even given the token and the cookie, a post from elsewhere is refused."""
    browser = Browser(hosted)
    page = browser.navigate("/login")

    posted = browser.fetch(
        "/login", data=credentials(rendered_token(page.text)), origin=ANOTHER_SITE
    )

    assert posted.status_code == 303
    assert "error=origin" in posted.headers["location"]
    assert browser.cookie(security.SESSION_COOKIE) is None


def test_the_wrong_password_cannot_sign_in(hosted: Serving) -> None:
    browser = Browser(hosted)
    page = browser.navigate("/login")

    posted = browser.fetch(
        "/login", data=credentials(rendered_token(page.text), password="not the password")
    )

    assert posted.status_code == 303
    assert "error=credentials" in posted.headers["location"]
    assert browser.cookie(security.SESSION_COOKIE) is None

    landed = browser.navigate("/login?error=credentials")

    assert landed.status_code == 200
    # Which half was wrong is not said, because saying it only helps a script.
    assert "username and password" in landed.text


def test_a_refused_sign_in_does_not_replay_the_password_on_refresh(hosted: Serving) -> None:
    """303 to a GET, so the back button and F5 reload a page rather than a POST."""
    browser = Browser(hosted)
    page = browser.navigate("/login")

    posted = browser.fetch(
        "/login", data=credentials(rendered_token(page.text), password="not the password")
    )

    assert posted.status_code == 303
    assert posted.headers["location"].startswith("/login?next=")


# ----------------------------------------------------- the proxy itself works


def test_the_page_is_built_with_the_address_the_browser_used(hosted: Serving) -> None:
    """Proof that uvicorn's proxy-header handling is on, not just that a header was sent.

    Without it every link the templates build would come out as ``http://`` and
    the browser would refuse to load it on an ``https`` page.
    """
    browser = Browser(hosted)
    page = browser.navigate("/login?next=%2Fregister")
    browser.fetch("/login", data=credentials(rendered_token(page.text)))

    protected = browser.navigate("/register")

    assert f"{PUBLIC}/static/" in protected.text
    assert f"http://{HOST}/static/" not in protected.text
