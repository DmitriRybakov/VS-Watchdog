"""What a colleague is shown when the database cannot be reached.

This was written down as an assumption - that a traceback reaches the browser -
and the assumption was half wrong, which is why it is a test now. Starlette
answers an unhandled exception with the two words ``Internal Server Error`` as
plain text and nothing else: no traceback, no driver message, no host name. What
was true is that those two words were all a colleague got, and that the command
line printed a thousand-line decorated traceback for the same failure.

The three messages below were recorded from the real driver rather than invented,
because the question is what a **driver** puts in a message, not what we would
have put there:

- the name that does not resolve, from a hostname under ``.invalid``;
- the refused password, from a socket on loopback speaking enough of the
  PostgreSQL protocol to answer a login with ``28P01``;
- the timeout, from a socket that accepts the connection and then says nothing.

None of the three carries the password. Two of them carry the host, the port or
the database user, which is why the page carries none of the message at all.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.exc import OperationalError

from watchdog.core.logging import configure_logging
from watchdog.core.settings import get_settings
from watchdog.storage.db import create_db_engine
from watchdog.storage.repository import Repository
from watchdog.web import security

# Shaped like the hosted service, valued like nothing at all.
PASSWORD = "DB-PASSWORD-NOBODY-SHOULD-SEE"
DATABASE_URL = f"postgresql+psycopg://dbuser:{PASSWORD}@db.invalid:5432/watchdog"
USERNAME = "entr"
SIGN_IN_PASSWORD = "a-password-nobody-would-guess"
SECRET = "x" * 48

CONNECTION_FAILURES = {
    "a name that does not resolve": psycopg.OperationalError(
        "failed to resolve host 'db.invalid': [Errno 11001] getaddrinfo failed"
    ),
    "a password the server refused": psycopg.OperationalError(
        'connection failed: connection to server at "10.20.30.40", port 5432 failed: '
        'FATAL:  password authentication failed for user "dbuser"'
    ),
    "a server that never answered": psycopg.errors.ConnectionTimeout("connection timeout expired"),
}

# Anything that would tell a stranger what this is built on, or tell a colleague
# nothing they can use. The password is here as well: it is not in any of the
# three messages today, and this is what says so if a driver ever changes.
NEVER_ON_THE_PAGE = (
    PASSWORD,
    "Traceback",
    "psycopg",
    "OperationalError",
    "sqlalche.me",
    "db.invalid",
    "dbuser",
    "10.20.30.40",
    "getaddrinfo",
)


def failing_repository(failure: Exception) -> Callable[[], Repository]:
    """Stand in for a repository whose first query cannot get a connection."""

    def unreachable_database() -> Repository:
        raise OperationalError("select 1", None, failure)

    return unreachable_database


@pytest.fixture
def hosted_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The hosted service, signed in, over a database that is not there.

    ``https`` because the session cookie is ``Secure`` in hosted mode and a test
    over plain http would silently never send it. No lifespan, because the
    startup handler is the one thing that opens the configured database for
    itself, and this test is about the requests that follow.
    """
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("AUTH_USERNAME", USERNAME)
    monkeypatch.setenv("AUTH_PASSWORD", SIGN_IN_PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    get_settings.cache_clear()

    from watchdog.web.app import create_app

    client = TestClient(create_app(), base_url="https://testserver", raise_server_exceptions=False)
    client.get("/login")
    signed_in = client.post(
        "/login",
        data={
            "username": USERNAME,
            "password": SIGN_IN_PASSWORD,
            "csrf_token": client.cookies[security.CSRF_COOKIE],
            "next": "/register",
        },
        follow_redirects=False,
    )
    assert signed_in.status_code == 303, "the test never got as far as a page"

    try:
        yield client
    finally:
        get_settings.cache_clear()


def unreachable(client: TestClient, failure: Exception, path: str = "/register") -> Response:
    from watchdog.web.deps import get_repository

    client.app.dependency_overrides[get_repository] = failing_repository(failure)
    return client.get(path, follow_redirects=False)


@pytest.mark.parametrize("kind", list(CONNECTION_FAILURES))
def test_a_connection_failure_is_a_short_page_and_not_an_internal_detail(
    hosted_client: TestClient, kind: str
) -> None:
    response = unreachable(hosted_client, CONNECTION_FAILURES[kind])

    assert response.status_code == 503, "a database that is down is not the browser's fault"
    assert response.headers["content-type"].startswith("text/html")
    assert "The database is temporarily unavailable" in response.text

    for detail in NEVER_ON_THE_PAGE:
        assert detail not in response.text, f"{detail!r} reached the browser on {kind}"


@pytest.mark.parametrize("path", ["/register", "/runs", "/settings", "/register/export.csv"])
def test_every_page_and_the_export_answer_the_same_way(
    hosted_client: TestClient, path: str
) -> None:
    """An export is a download, and a download that fails must still say so in words."""
    response = unreachable(hosted_client, CONNECTION_FAILURES["a server that never answered"], path)

    assert response.status_code == 503
    assert "The database is temporarily unavailable" in response.text


def test_the_reason_is_written_to_the_log_where_it_can_be_acted_on(
    hosted_client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    """The half of the split that matters: the page loses nothing, the log keeps it."""
    configure_logging("INFO", json_output=True)
    capsys.readouterr()

    unreachable(hosted_client, CONNECTION_FAILURES["a password the server refused"])
    logged = capsys.readouterr().out

    assert "database_unavailable" in logged
    assert "password authentication failed" in logged, "the diagnosis was thrown away"
    assert "postgresql://db.invalid:5432/watchdog" in logged, "the log must name the database"
    assert PASSWORD not in logged, "the log named the database by its whole URL"


def test_health_still_answers_while_the_database_is_down(hosted_client: TestClient) -> None:
    """What a monitor watches must not depend on the thing it is meant to report."""
    from watchdog.web.deps import get_repository

    hosted_client.app.dependency_overrides[get_repository] = failing_repository(
        CONNECTION_FAILURES["a name that does not resolve"]
    )

    response = hosted_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "watchdog"}


def test_debug_output_is_off_in_a_hosted_process(hosted_client: TestClient) -> None:
    """Two switches, either of which would put internals on a page or in a log."""
    assert hosted_client.app.debug is False
    assert create_db_engine(DATABASE_URL).echo is False


# ------------------------------------------------- the half of a page HTMX drives


def test_an_htmx_request_gets_the_same_refusal_rather_than_a_swap(
    hosted_client: TestClient,
) -> None:
    """HTMX does not swap a 5xx, so this response is only ever seen by the script.

    Which is the point of it: a filter, a button or the status poll failing would
    otherwise change nothing on screen and say nothing at all.
    """
    from watchdog.web.deps import get_repository

    hosted_client.app.dependency_overrides[get_repository] = failing_repository(
        CONNECTION_FAILURES["a server that never answered"]
    )

    response = hosted_client.get("/register", headers={"HX-Request": "true"})

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"

    for detail in NEVER_ON_THE_PAGE:
        assert detail not in response.text


def test_every_page_carries_the_script_that_turns_that_into_a_banner(
    client: TestClient,
) -> None:
    """That the script is loaded and served. What it *does* is checked in a browser.

    Asserting that the file contains an event name would prove nothing about the
    interaction: the banner appearing, its buttons surviving feedback mode, the
    retry carrying the same filters and a failed save outliving the next poll
    were all checked by driving a real browser against a disposable database.
    """
    html = client.get("/register").text
    script = client.get("/static/js/outage.js")

    assert "/static/js/outage.js" in html
    assert script.status_code == 200


# --------------------------------------------------------------- the command line


def test_the_command_line_says_it_in_one_sentence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every command shares this failure, so the entry point answers it once."""
    from watchdog import cli

    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    get_settings.cache_clear()

    def fail() -> None:
        raise OperationalError(
            "select 1", None, CONNECTION_FAILURES["a server that never answered"]
        )

    monkeypatch.setattr(cli, "app", fail)

    with pytest.raises(SystemExit) as exit_code:
        cli.run()

    assert exit_code.value.code == 1
    printed = capsys.readouterr()
    everything = printed.out + printed.err

    assert "The database could not be reached." in everything
    assert "postgresql://db.invalid:5432/watchdog" in everything, "say which database it tried"
    assert PASSWORD not in everything
    assert "Traceback" not in everything
    assert len(everything.splitlines()) < 10, "a colleague gets a sentence, not a stack trace"
