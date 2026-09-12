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
