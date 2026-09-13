from __future__ import annotations

from watchdog.core.settings import Settings


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
