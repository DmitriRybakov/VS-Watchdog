"""What a test run reads from the environment, and what it refuses to read.

The suite may be started from a window where ``deploy\\Use-DeployEnv.ps1`` has been
dot-sourced. That window holds the hosted DATABASE_URL, AUTH_USERNAME,
AUTH_PASSWORD, SESSION_SECRET and LLM_API_KEY, and every Python process started
there inherits them - which is enough to switch the shared sign-in on for the
whole suite and send every page test to a login form.

These tests are about the fixtures, not the application. Reading real environment
variables is exactly how the application is configured in production, and
tests/unit/test_settings.py holds the proof that it still does.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.conftest import TEST_DATABASE_URL, isolated_environment

from watchdog.core.settings import Settings, get_settings
from watchdog.storage.repository import Repository

# Shaped like a real deployment, valued like nothing at all. Long enough to pass
# the session-secret length rule, so the pollution is realistic rather than
# something the application would reject on its own.
DEPLOYMENT_SHAPED = {
    "ENVIRONMENT": "production",
    "RENDER": "true",
    "DATABASE_URL": "postgresql+psycopg://dummy:dummy@db.invalid:5432/dummy",
    "AUTH_USERNAME": "dummy-user",
    "AUTH_PASSWORD": "dummy-password",
    "SESSION_SECRET": "dummy-secret-" + "d" * 40,
    "LLM_API_KEY": "dummy-key",
    "LLM_PROVIDER": "azure_openai",
}


def test_the_suite_runs_against_an_isolated_database_and_never_the_real_register() -> None:
    settings = get_settings()

    assert settings.database_url == TEST_DATABASE_URL
    assert "watchdog.db" not in settings.database_url
    assert ":memory:" in settings.database_url


def test_the_suite_runs_with_the_sign_in_off_and_no_model_credential() -> None:
    settings = get_settings()

    assert settings.auth_required is False
    assert settings.is_hosted is False
    assert settings.llm_provider == "disabled"
    assert settings.llm_api_key is None


def test_a_deployment_shaped_terminal_cannot_reach_the_application(
    monkeypatch: pytest.MonkeyPatch, repository: Repository
) -> None:
    """The regression: the polluted window, reproduced, with the harness applied.

    Every variable ``Use-DeployEnv.ps1`` sets is present here before the fixtures
    do their work, which is the situation that produced eighty-six failures. What
    is asserted is what a page test asserts: the register renders, rather than a
    sign-in page.
    """
    for name, value in DEPLOYMENT_SHAPED.items():
        monkeypatch.setenv(name, value)

    with isolated_environment():
        from watchdog.web.app import create_app
        from watchdog.web.deps import get_repository

        settings = get_settings()
        assert settings.auth_required is False
        assert settings.database_url == TEST_DATABASE_URL

        app = create_app()
        app.dependency_overrides[get_repository] = lambda: repository

        with TestClient(app) as client:
            response = client.get("/register", follow_redirects=False)

        assert response.status_code == 200
        assert "/login" not in response.headers.get("location", "")


def test_an_env_file_on_one_laptop_cannot_decide_what_the_suite_is_testing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``.env`` is read relative to the working directory, so it is a second door.

    The first half is the application's real behaviour and must keep working: a
    laptop is configured by that file, so the file is read when the setting says
    to read it. The second half is the harness holding the door shut for the
    length of a test run.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        f"AUTH_USERNAME=from-the-file\nAUTH_PASSWORD=from-the-file\nSESSION_SECRET={'f' * 48}\n",
        encoding="utf-8",
    )

    with pytest.MonkeyPatch.context() as as_shipped:
        as_shipped.setitem(Settings.model_config, "env_file", ".env")
        assert Settings().auth_required is True, "a .env file is how a laptop is configured"

    with isolated_environment():
        assert Settings().auth_required is False


# ------------------------------------------------------- what an import may do
#
# The isolation fixture runs after every module pytest collects has been
# imported. So an import that read DATABASE_URL, built an engine or opened a
# connection would do it against whatever the window held, before the harness had
# any say - and it would leave no mark anywhere a person would look.

IMPORT_CHECK = """
import json, pathlib, socket

attempted = []
socket.socket.connect = lambda self, address: attempted.append(address)

import watchdog.cli
import watchdog.storage.repository
import watchdog.web.templating
import watchdog.web.routes.register

from watchdog.core.settings import get_settings
from watchdog.storage.db import get_session_factory

before_entrypoint = {
    "settings_read": get_settings.cache_info().currsize,
    "engines_built": get_session_factory.cache_info().currsize,
}

# The module uvicorn loads. It builds the ASGI object at import on purpose, so it
# is the one import that does read settings - and still must not reach a database.
import watchdog.web.app

print(json.dumps({
    "before_entrypoint": before_entrypoint,
    "engines_built_after_entrypoint": get_session_factory.cache_info().currsize,
    "connections": [str(address) for address in attempted],
    "files_left_behind": sorted(path.name for path in pathlib.Path.cwd().iterdir()),
}))
"""


def test_importing_the_application_reaches_no_database_and_holds_no_configuration(
    tmp_path: Path,
) -> None:
    """Imported in a fresh process, in a window shaped like a deployment.

    DATABASE_URL points at a host that does not exist, so any connection attempt
    is a socket this test can see, and the working directory is empty, so a
    SQLite file opened by accident has nowhere to hide.
    """
    environment = {**os.environ, **DEPLOYMENT_SHAPED, "PYTHONIOENCODING": "utf-8"}

    completed = subprocess.run(
        [sys.executable, "-c", IMPORT_CHECK],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.splitlines()[-1])

    assert result["connections"] == [], "an import opened a connection"
    assert result["files_left_behind"] == [], "an import created a file"
    assert result["before_entrypoint"] == {"settings_read": 0, "engines_built": 0}, (
        "a module read the configuration at import, before any fixture could set it"
    )
    assert result["engines_built_after_entrypoint"] == 0, "importing the app built an engine"
