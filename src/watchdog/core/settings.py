"""Application settings.

Every value comes from the environment, so the only difference between a laptop,
Render and Azure is environment variables. Nothing here touches the database,
the network or the filesystem at import time: call ``get_settings()``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(default="sqlite:///data/watchdog.db")

    # "disabled" is the default; the whole application must work with it.
    llm_provider: str = Field(default="disabled")
    llm_model: str | None = Field(default=None)
    llm_api_key: str | None = Field(default=None)
    llm_endpoint: str | None = Field(default=None)

    log_level: str = Field(default="INFO")
    data_dir: Path = Field(default=Path("data"))

    environment: str = Field(default="dev")

    @property
    def is_dev(self) -> bool:
        return self.environment.lower() in {"dev", "development", "local"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, read from the environment once."""
    return Settings()
