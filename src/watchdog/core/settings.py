"""Application settings.

Every value comes from the environment, so the only difference between a laptop,
Render and Azure is environment variables. Nothing here touches the database,
the network or the filesystem at import time: call ``get_settings()``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    # Azure dates its API rather than versioning it by name.
    llm_api_version: str = Field(default="2024-10-21")
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    # Whether to send temperature, top_p and seed. "auto" asks the model name:
    # reasoning models reject them rather than ignoring them.
    llm_sampling: Literal["auto", "on", "off"] = Field(default="auto")
    # How much notice text one assessment may send. The title is never cut.
    llm_notice_chars: int = Field(default=6000, ge=500, le=60000)
    # How many notices are assessed at once. Four is the ceiling on purpose.
    llm_concurrency: int = Field(default=4, ge=1, le=4)

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
