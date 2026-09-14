from __future__ import annotations

import pytest
import structlog
from pydantic import ValidationError

from watchdog.core.settings import Settings, get_settings


def test_defaults_let_a_fresh_clone_run(monkeypatch) -> None:
    for name in (
        "DATABASE_URL",
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_API_KEY",
        "LLM_ENDPOINT",
        "LOG_LEVEL",
        "DATA_DIR",
        "ENVIRONMENT",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.database_url == "sqlite:///data/watchdog.db"
    assert settings.llm_provider == "disabled"
    assert settings.log_level == "INFO"
    assert str(settings.data_dir) == "data"


def test_unknown_optional_values_are_none_not_empty_string(monkeypatch) -> None:
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    settings = Settings(_env_file=None)

    assert settings.llm_model is None
    assert settings.llm_api_key is None


def test_environment_variables_override_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user@host/db")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+psycopg://user@host/db"
    assert settings.log_level == "DEBUG"


# ---------------------------------------------------------------- hosted mode
#
# Every one of these is a failure that otherwise looks like a success: the site
# comes up, and either anybody can read it or every review is lost at the next
# deploy. Refusing to start is the only version that announces itself.


def hosted(**values: str) -> Settings:
    """A hosted process configured with exactly these values and no .env file."""
    return Settings(_env_file=None, environment="production", **values)


def test_a_laptop_needs_no_sign_in_and_no_postgres() -> None:
    settings = Settings(_env_file=None, environment="dev")

    assert settings.is_hosted is False
    assert settings.auth_required is False
    assert settings.startup_problems() == []


def test_a_hosted_process_without_a_password_refuses_to_start() -> None:
    problems = hosted(database_url="postgresql://u:p@host/db").startup_problems()

    assert any("AUTH_USERNAME" in problem for problem in problems)
    assert any("open to anyone" in problem for problem in problems)


def test_a_hosted_process_without_a_session_secret_refuses_to_start() -> None:
    problems = hosted(
        database_url="postgresql://u:p@host/db", auth_username="a", auth_password="b"
    ).startup_problems()

    assert any("SESSION_SECRET" in problem for problem in problems)


def test_a_hosted_process_will_not_quietly_fall_back_to_sqlite() -> None:
    problems = hosted(
        auth_username="a", auth_password="b", session_secret="s" * 40
    ).startup_problems()

    assert len(problems) == 1
    assert "PostgreSQL" in problems[0]
    assert "wiped" in problems[0]


def test_a_fully_configured_hosted_process_starts() -> None:
    settings = hosted(
        database_url="postgresql://u:p@host/db",
        auth_username="a",
        auth_password="b",
        session_secret="s" * 40,
    )

    assert settings.startup_problems() == []
    assert settings.auth_required is True


def test_render_is_treated_as_hosted_even_if_environment_was_forgotten() -> None:
    """Render sets RENDER on every service. Leaving ENVIRONMENT at its default
    must not be the difference between a sign-in and an open site."""
    settings = Settings(_env_file=None, environment="dev", render="true")

    assert settings.is_hosted is True
    assert settings.auth_required is True
    assert settings.startup_problems() != []


def test_half_configured_sign_in_is_refused_rather_than_ignored() -> None:
    settings = Settings(_env_file=None, environment="dev", auth_username="a")

    assert any("must be set together" in problem for problem in settings.startup_problems())


def test_a_short_session_secret_is_refused() -> None:
    settings = Settings(_env_file=None, environment="dev", session_secret="too short")

    assert any("32 characters" in problem for problem in settings.startup_problems())


# ------------------------------------------------------------------- secrets
#
# A settings object is printed by anything that fails while holding one: a log
# line, a traceback, an assertion failure. Those are the paths ``repr=False``
# closes, and they are the ones tested first. The paths it does not close are
# tested straight afterwards, so nobody reads more into it than it does.

SECRETS = ("DB-PASSWORD", "AUTH-PASSWORD", "SESSION-SECRET", "MODEL-KEY")


def configured() -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://dbuser:DB-PASSWORD@db.example:5432/watchdog",
        auth_username="entr",
        auth_password="AUTH-PASSWORD",
        session_secret="SESSION-SECRET" + "x" * 40,
        llm_api_key="MODEL-KEY",
    )


@pytest.mark.parametrize("show", [repr, str, "{}".format])
def test_no_credential_can_be_printed(show) -> None:
    shown = show(configured())

    for secret in SECRETS:
        assert secret not in shown, f"{secret} reached a printed settings object"


def test_a_log_line_holding_the_settings_object_carries_no_credential() -> None:
    """structlog renders a value with repr, which is the path that leaked one."""
    rendered = structlog.processors.KeyValueRenderer()(
        None, "info", {"event": "startup", "settings": configured()}
    )

    for secret in SECRETS:
        assert secret not in rendered


def test_a_validation_failure_elsewhere_does_not_echo_the_credentials() -> None:
    """One bad value must not drag the rest of the configuration into the message."""
    with pytest.raises(ValidationError) as caught:
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://dbuser:DB-PASSWORD@db.example/watchdog",
            session_secret="SESSION-SECRET" + "x" * 40,
            llm_concurrency=99,
        )

    for secret in SECRETS:
        assert secret not in str(caught.value)


def test_what_repr_false_does_not_cover_is_written_down_rather_than_assumed() -> None:
    """The limit of the protection, in the form of the thing it does not do.

    Marking a field unprintable hides it from the representation and from nothing
    else. Nothing in this application dumps a settings object or builds a message
    out of a credential, and this test exists so that changing that is a decision
    rather than an accident.
    """
    dumped = configured().model_dump()

    assert dumped["session_secret"].startswith("SESSION-SECRET")
    assert "DB-PASSWORD" in dumped["database_url"]


def test_the_values_are_still_readable_by_the_code_that_needs_them() -> None:
    """Unprintable, not unreadable: sign-in and the database still work."""
    settings = configured()

    assert settings.auth_password == "AUTH-PASSWORD"
    assert settings.session_secret.startswith("SESSION-SECRET")  # type: ignore[union-attr]
    assert settings.database_url.endswith("/watchdog")
    assert settings.auth_configured is True


def test_what_is_not_a_secret_stays_legible() -> None:
    """Hiding everything would make a configuration problem impossible to read."""
    shown = repr(configured())

    assert "entr" in shown, "the user name is not a secret and naming it aids diagnosis"
    assert "environment=" in shown


def test_a_startup_problem_names_the_variable_and_never_its_value() -> None:
    problems = Settings(
        _env_file=None, environment="production", session_secret="too short"
    ).startup_problems()

    assert any("SESSION_SECRET" in problem for problem in problems)
    assert not any("too short" in problem for problem in problems)


# ------------------------------------------------- naming the database safely
#
# A connection that fails is nearly always a service pointed at the wrong
# database. Printing the URL leaks the password; printing nothing leaves the one
# question unanswerable.


def test_the_database_can_be_named_without_naming_the_credential() -> None:
    target = configured().database_target

    assert target == "postgresql://db.example:5432/watchdog"
    assert "DB-PASSWORD" not in target
    assert "dbuser" not in target


def test_a_password_containing_an_at_sign_is_still_removed() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql://dbuser:p@ss@word@db.example:5432/watchdog",
    )

    assert settings.database_target == "postgresql://db.example:5432/watchdog"


def test_the_connection_options_are_dropped_with_the_credential() -> None:
    """A query string can carry a certificate path or a password of its own."""
    settings = Settings(
        _env_file=None,
        database_url="postgresql://u:p@db.example/watchdog?sslmode=require&password=SECOND",
    )

    assert settings.database_target == "postgresql://db.example/watchdog"


def test_a_sqlite_file_is_named_as_it_stands() -> None:
    settings = Settings(_env_file=None, database_url="sqlite:///data/watchdog.db")

    assert settings.database_target == "sqlite:///data/watchdog.db"


# --------------------------------------- the environment is how production works
#
# The tests above build a Settings object directly. These two prove the thing the
# hosted service depends on: the process reads its configuration from the
# environment it was started in, and reads it once.


def test_the_process_wide_settings_read_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db.example/watchdog")
    monkeypatch.setenv("AUTH_USERNAME", "entr")
    monkeypatch.setenv("AUTH_PASSWORD", "a-password")
    monkeypatch.setenv("SESSION_SECRET", "s" * 48)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.is_hosted is True
    assert settings.auth_required is True
    assert settings.database_url.startswith("postgresql")
    assert settings.startup_problems() == []


def test_the_environment_is_read_once_and_then_held(monkeypatch) -> None:
    """Changing a variable on a running service changes nothing until it restarts."""
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    get_settings.cache_clear()

    first = get_settings()
    monkeypatch.setenv("LOG_LEVEL", "ERROR")

    assert get_settings() is first
    assert get_settings().log_level == "DEBUG"
