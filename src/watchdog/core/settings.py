"""Application settings.

Every value comes from the environment, so the only difference between a laptop,
Render and Azure is environment variables. Nothing here touches the database,
the network or the filesystem at import time: call ``get_settings()``.

Four of these values are credentials and are marked ``repr=False``. That covers
exactly one thing, and it is worth knowing which: the object's representation.
So a log line holding the settings object, a traceback frame and a failed
assertion are all safe, and were the way a secret escaped once.

It does **not** cover ``model_dump()``, ``model_dump_json()``, ``dict(settings)``,
``settings.__dict__``, printing a field directly, or a validation error echoing a
rejected value. Nothing in this application does any of those with a credential -
the rule is that a secret is read at the point it is used and never assembled
into a message - and tests/unit/test_settings.py pins both halves.
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

    # Carries the database password on a hosted service, so it is never printed.
    database_url: str = Field(default="sqlite:///data/watchdog.db", repr=False)

    # "disabled" is the default; the whole application must work with it.
    llm_provider: str = Field(default="disabled")
    llm_model: str | None = Field(default=None)
    llm_api_key: str | None = Field(default=None, repr=False)
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

    # The shared sign-in for a hosted pilot. One username, one password, one team.
    # Unset on a laptop, which is why a laptop needs no sign-in.
    auth_username: str | None = Field(default=None)
    auth_password: str | None = Field(default=None, repr=False)
    # Signs the session cookie. Changing it signs everybody out, which is the
    # only way to end a session that is already issued.
    session_secret: str | None = Field(default=None, repr=False)
    session_hours: int = Field(default=12, ge=1, le=720)

    # Set by Render on every service it runs. Read only so that a hosted service
    # with ENVIRONMENT left at its default is still treated as hosted rather than
    # served open to the internet.
    render: str | None = Field(default=None)

    @property
    def is_dev(self) -> bool:
        return self.environment.lower() in {"dev", "development", "local"}

    @property
    def is_hosted(self) -> bool:
        """True anywhere that is not a laptop. Decides what is compulsory."""
        return not self.is_dev or bool(self.render)

    @property
    def auth_configured(self) -> bool:
        return bool(self.auth_username and self.auth_password and self.session_secret)

    @property
    def database_target(self) -> str:
        """Which database this is, with nothing in it that could open the database.

        What to print when a connection fails. The whole URL carries the password
        and printing nothing at all is no better: the usual fault is a service
        pointed at the wrong database, and the host and the name are what say so.
        """
        scheme, separator, rest = self.database_url.partition("://")
        if not separator:
            return self.database_url
        # Everything before the last @ is the user name and the password.
        _, _, host_and_name = rest.rpartition("@")
        return f"{scheme.partition('+')[0]}://{host_and_name.partition('?')[0]}"

    @property
    def auth_required(self) -> bool:
        """Whether every route except /health needs a signed-in session.

        On by default anywhere hosted. Off on a laptop unless the three auth
        values are set, which is how it gets tested before it is deployed.
        """
        return self.is_hosted or self.auth_configured

    def startup_problems(self) -> list[str]:
        """What would make this process unsafe to start, in plain sentences.

        Hosted has no safe fallback for any of these: open access and a SQLite
        file on a disk that is wiped at every deploy are both worse than refusing
        to start, because neither announces itself.
        """
        problems: list[str] = []

        if self.is_hosted:
            if not self.auth_username or not self.auth_password:
                problems.append(
                    "AUTH_USERNAME and AUTH_PASSWORD are not both set, so the site would be "
                    "open to anyone who finds the address."
                )
            if not self.session_secret:
                problems.append(
                    "SESSION_SECRET is not set, so a sign-in could not be kept between requests."
                )
            if self.database_url.startswith("sqlite"):
                problems.append(
                    "DATABASE_URL is not set to a PostgreSQL database. A hosted disk is wiped "
                    "on every deploy, so a SQLite file there would lose every review."
                )
        elif (self.auth_username or self.auth_password or self.session_secret) and (
            not self.auth_configured
        ):
            problems.append(
                "AUTH_USERNAME, AUTH_PASSWORD and SESSION_SECRET must be set together. "
                "Set all three to require sign-in, or none of them to run without it."
            )

        if self.session_secret is not None and len(self.session_secret) < 32:
            problems.append(
                "SESSION_SECRET is shorter than 32 characters. Generate a long random one; "
                "it is the only thing standing between a guess and a valid session."
            )

        return problems


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, read from the environment once."""
    return Settings()
