"""No credential reaches anything a person or a log file can read.

``tests/unit/test_settings.py`` pins what the settings object does with a
credential. That is worth having and it is not this: it would not have caught a
log line assembled by hand somewhere else, which is exactly the bug that put the
Supabase password into a terminal - ``watchdog config show`` printed the whole
``DATABASE_URL``, and every test passed while it did.

So these tests assert on **output**. Four dummy credentials are set, the
application is driven the way it is really driven, and everything it produced is
searched for them:

- every command the CLI has, including the ones that fail;
- the log lines structlog actually renders, not a renderer built in the test;
- every HTTP response, including the error pages, which are the ones written in a
  hurry and never looked at again.

Nothing here reaches the network. TED and any model are refused at the socket,
and the database is a port on loopback that nothing listens on - a refused
connection, instantly, which is also the failure path where a connection string
is most likely to be printed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
import typer.main
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from watchdog.cli import app as cli_app
from watchdog.core.logging import configure_logging, get_logger
from watchdog.core.settings import Settings, get_settings
from watchdog.storage.repository import Repository
from watchdog.web import security

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

# Shaped like the hosted service. Distinctive enough that finding one in a stream
# of output is unambiguous, and valueless.
DB_PASSWORD = "DB-PASSWORD-NOBODY-SHOULD-SEE"
SIGN_IN_PASSWORD = "AUTH-PASSWORD-NOBODY-SHOULD-SEE"
SESSION_SECRET = "SESSION-SECRET-NOBODY-SHOULD-SEE" + "x" * 20
MODEL_KEY = "MODEL-KEY-NOBODY-SHOULD-SEE"
SECRETS = (DB_PASSWORD, SIGN_IN_PASSWORD, SESSION_SECRET, MODEL_KEY)

USERNAME = "entr"

# A host name that cannot resolve, so the connection fails at once and nothing
# leaves the machine. ``connect_timeout`` is the belt: a network whose resolver
# answers everything would otherwise leave each command waiting.
DATABASE_URL = (
    f"postgresql+psycopg://dbuser:{DB_PASSWORD}@db.invalid:5432/watchdog?connect_timeout=2"
)

DEPLOYMENT_SHAPED = {
    "ENVIRONMENT": "production",
    "DATABASE_URL": DATABASE_URL,
    "AUTH_USERNAME": USERNAME,
    "AUTH_PASSWORD": SIGN_IN_PASSWORD,
    "SESSION_SECRET": SESSION_SECRET,
    "LLM_PROVIDER": "azure_openai",
    "LLM_MODEL": "gpt-4o-mini",
    "LLM_API_KEY": MODEL_KEY,
    "LLM_ENDPOINT": "https://entr.invalid/openai",
    "LOG_LEVEL": "DEBUG",
}


def found_in(text: str) -> list[str]:
    """Which credentials are in this text. Named, so a failure says which one."""
    return [secret for secret in SECRETS if secret in text]


# ------------------------------------------------------------ the command line


def command_names() -> list[list[str]]:
    """Every command the CLI has, read from the CLI rather than listed by hand.

    A command added next year is covered by this test on the day it is added,
    which is the only version of "every command" that stays true.
    """
    found: list[list[str]] = []

    def walk(command: object, path: list[str]) -> None:
        children = getattr(command, "commands", None)
        if not children:
            found.append(path)
            return
        for name, child in sorted(children.items()):
            walk(child, [*path, name])

    walk(typer.main.get_command(cli_app), [])
    return found


# Runs one command the way the installed script runs it, with the network
# refused and the retry sleeps removed so a command that would retry a request
# five times fails at once instead.
RUNNER = textwrap.dedent(
    """
    import socket, sys, time, traceback

    time.sleep = lambda *args, **kwargs: None

    def refuse(*args, **kwargs):
        raise OSError("this test refuses the network")

    socket.socket.connect = refuse
    socket.create_connection = refuse

    from watchdog.cli import run

    sys.argv = ["watchdog", *sys.argv[1:]]
    try:
        run()
    except SystemExit:
        pass
    except BaseException:
        # Printed rather than raised: a traceback is output too, and output is
        # the whole subject of this test.
        traceback.print_exc()
    """
)


@pytest.fixture(scope="module")
def command_output() -> dict[str, str]:
    """Everything every command printed, in one process shaped like a deployment."""
    environment = {**os.environ, **DEPLOYMENT_SHAPED, "PYTHONIOENCODING": "utf-8"}
    output: dict[str, str] = {}

    for name in command_names():
        completed = subprocess.run(
            [sys.executable, "-c", RUNNER, *name],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            # Decoded here rather than by whatever code page this console is on:
            # a notice in Norwegian in a listing must not become a decode error
            # in the test that reads it.
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        output[" ".join(name)] = completed.stdout + completed.stderr

    return output


def test_every_command_has_something_to_say(command_output: dict[str, str]) -> None:
    """Guards the test above it: a command that printed nothing proves nothing."""
    assert len(command_output) >= 12, "the command list is not being read from the CLI"

    silent = [name for name, text in command_output.items() if not text.strip()]
    assert silent == [], f"these commands printed nothing, so nothing was checked: {silent}"


def test_no_command_prints_a_credential(command_output: dict[str, str]) -> None:
    leaked = {name: found_in(text) for name, text in command_output.items() if found_in(text)}

    assert leaked == {}, f"a credential reached the terminal: {leaked}"


def test_the_commands_that_name_the_database_name_it_without_the_password(
    command_output: dict[str, str],
) -> None:
    """The real leak, in the place it happened. ``config show`` printed the URL."""
    naming = {name: text for name, text in command_output.items() if "db.invalid" in text}

    assert naming, "no command named the database at all, so this proves nothing"
    for name, text in naming.items():
        assert DB_PASSWORD not in text, f"{name} printed the password with the database"


# ------------------------------------------------------------------- the log


def test_the_log_structlog_actually_renders_carries_no_credential(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rendered by the configured pipeline, in the JSON form a hosted service emits.

    The settings object goes in whole, which is the shape that leaked one: a log
    line built in a hurry with the configuration attached to it.
    """
    for name, value in DEPLOYMENT_SHAPED.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    settings = get_settings()

    configure_logging(settings.log_level, json_output=True)
    capsys.readouterr()

    log = get_logger("test")
    log.info("effective_configuration", settings=settings)
    log.info("named_safely", database=settings.database_target)
    log.error("startup_refused", problems=settings.startup_problems())

    rendered = capsys.readouterr().out

    assert "effective_configuration" in rendered, "nothing was captured, so nothing was checked"
    leaked = found_in(rendered)
    assert leaked == [], f"a credential was rendered into the log: {leaked}"


def test_the_log_is_searched_in_a_way_that_would_find_a_credential(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same capture, on the one path the settings object cannot close.

    ``model_dump_json`` is not covered by ``repr=False``. Nothing in Watchdog
    calls it; logging it here proves two things at once - that a leak of this
    shape would be caught by the test above rather than passing quietly, and that
    dumping the configuration into a log line is still the way to cause one.
    """
    for name, value in DEPLOYMENT_SHAPED.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()

    configure_logging("INFO", json_output=True)
    capsys.readouterr()

    get_logger("test").warning(
        "a_line_nobody_should_write", configuration=get_settings().model_dump_json()
    )

    assert sorted(found_in(capsys.readouterr().out)) == sorted(SECRETS)


def test_the_settings_object_is_not_what_protects_the_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Why the tests above assert on output and not on the object.

    ``repr=False`` hides a credential from a printed settings object and from
    nothing else. What keeps it out of the log is that no line is written this
    way, which only an assertion about output can check.
    """
    for name, value in DEPLOYMENT_SHAPED.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()

    dumped = json.loads(get_settings().model_dump_json())

    assert DB_PASSWORD in dumped["database_url"]
    assert dumped["auth_password"] == SIGN_IN_PASSWORD


# ------------------------------------------------------------ HTTP responses


@pytest.fixture
def hosted_client(monkeypatch: pytest.MonkeyPatch, repository: Repository) -> Iterator[TestClient]:
    """The hosted service with all four credentials set, over an empty database.

    ``https`` because the session cookie is ``Secure`` in hosted mode, and a test
    over plain http would never send it and would only ever see the sign-in page.
    """
    for name, value in DEPLOYMENT_SHAPED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()

    from watchdog.web.app import create_app
    from watchdog.web.deps import get_repository

    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repository

    client = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    try:
        yield client
    finally:
        get_settings.cache_clear()


def sign_in(client: TestClient) -> None:
    client.get("/login")
    client.post(
        "/login",
        data={
            "username": USERNAME,
            "password": SIGN_IN_PASSWORD,
            "csrf_token": client.cookies[security.CSRF_COOKIE],
            "next": "/register",
        },
        follow_redirects=False,
    )


def test_no_response_carries_a_credential_including_the_error_pages(
    hosted_client: TestClient,
) -> None:
    """Pages that work, refusals, a page that does not exist, and the two failures.

    The failure pages are the point. They are the ones written in a hurry, and an
    error page is where a connection string gets printed because whoever wrote it
    was trying to make the next failure easier to diagnose.
    """
    from watchdog.web.deps import get_repository

    responses = {
        "sign-in page": hosted_client.get("/login"),
        "health": hosted_client.get("/health"),
        "wrong password": hosted_client.post(
            "/login",
            data={
                "username": USERNAME,
                "password": "not-the-password",
                "csrf_token": hosted_client.cookies.get(security.CSRF_COOKIE, ""),
            },
            follow_redirects=False,
        ),
        "no session": hosted_client.get("/register", follow_redirects=False),
        "no such page": hosted_client.get("/no-such-page", follow_redirects=False),
    }

    sign_in(hosted_client)

    for path in ("/register", "/runs", "/settings", "/register/export.csv", "/api/docs"):
        responses[f"signed in {path}"] = hosted_client.get(path, follow_redirects=False)

    assert responses["signed in /register"].status_code == 200, (
        "the signed-in pages never rendered, so only failures were checked"
    )

    def unreachable_database() -> Repository:
        raise OperationalError("select 1", None, psycopg.OperationalError(DATABASE_URL))

    hosted_client.app.dependency_overrides[get_repository] = unreachable_database
    responses["database unavailable"] = hosted_client.get("/register", follow_redirects=False)

    assert responses["database unavailable"].status_code == 503, (
        "the failure page was not reached, so the pages most likely to leak were not checked"
    )

    for name, response in responses.items():
        text = response.text + json.dumps(dict(response.headers))
        assert found_in(text) == [], f"a credential reached the {name} response"
