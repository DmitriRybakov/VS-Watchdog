"""The shape of config/sources/ted.yaml, validated on the way in.

Configuration is data. Nothing here is hardcoded anywhere else, and a value that
cannot be expressed at the API is not accepted here either - a key people read
and believe has to be true.

CPV codes are normalised to eight-character strings here, once, so that a code
written as ``09300000`` in YAML survives PyYAML turning it into an integer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from watchdog.core.countries import known_country_codes
from watchdog.core.cpv import normalise_cpv
from watchdog.core.enums import ContractNature, NoticeStage

DEFAULT_CONFIG_PATH = Path("config/sources/ted.yaml")

# TED rejects a limit above this outright, with SEARCH_EXCEEDS_MAX_LIMIT.
MAX_PAGE_SIZE = 250


class TedSourceConfig(BaseModel):
    """What to ask TED for. Every field is data from config/sources/ted.yaml."""

    model_config = ConfigDict(frozen=True)

    cpv_prefixes: list[str] = Field(default_factory=list)
    # Where the buying organisation sits, ISO 3166-1 alpha-3.
    buyer_countries: list[str] = Field(default_factory=list)
    # Where the work happens. Empty means anywhere, which is not the same question.
    place_of_performance: list[str] = Field(default_factory=list)
    contract_natures: list[ContractNature] = Field(default_factory=list)
    notice_stages: list[NoticeStage] = Field(default_factory=list)
    extra_query: str | None = None

    # Whole days, because TED's query language has no hour: the publication-date
    # field only accepts YYYYMMDD or today(+/-n).
    overlap_days: int = Field(default=2, ge=0, le=30)
    # How far back the very first run reaches, when there is no watermark yet.
    backfill_days: int = Field(default=30, ge=1, le=365)
    page_size: int = Field(default=MAX_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)

    @field_validator("cpv_prefixes", mode="before")
    @classmethod
    def _normalise_cpv(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value

        codes: list[str] = []
        for item in value:
            code = normalise_cpv(item)
            if code is None:
                raise ValueError(
                    f"{item!r} is not a CPV code; write it as eight digits, "
                    "quoted if it starts with a zero"
                )
            codes.append(code)
        return codes

    @field_validator("buyer_countries", "place_of_performance", mode="before")
    @classmethod
    def _normalise_countries(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value

        known = known_country_codes()
        codes: list[str] = []
        for item in value:
            code = str(item).strip().upper()
            if len(code) != 3 or not code.isalpha():
                raise ValueError(
                    f"{item!r} is not an ISO 3166-1 alpha-3 country code, e.g. NOR or DEU"
                )
            if code not in known:
                raise ValueError(
                    f"{code!r} is not a country watchdog.core.countries can name; "
                    "add it there first so the register does not show a bare code"
                )
            codes.append(code)
        return codes


def parse_ted_config(data: dict[str, Any]) -> TedSourceConfig:
    """Validate an already-loaded mapping. Pure, so it is easy to test."""
    return TedSourceConfig.model_validate(data or {})


def load_ted_config(path: Path | str = DEFAULT_CONFIG_PATH) -> TedSourceConfig:
    """Read the seed file.

    Callers are the service layer only. When the config_version table becomes the
    active source of configuration, this stays as the seed loader and the service
    reads the active version instead.
    """
    text = Path(path).read_text(encoding="utf-8")
    return parse_ted_config(yaml.safe_load(text) or {})
