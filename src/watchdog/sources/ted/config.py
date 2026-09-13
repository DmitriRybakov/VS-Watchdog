"""The shape of config/sources/ted.yaml, validated on the way in.

Configuration is data. Nothing here is hardcoded anywhere else, and a value that
cannot be expressed at the API is not accepted here either - a key people read
and believe has to be true.

CPV codes are normalised to eight-character strings here, once, so that a code
written as ``09300000`` in YAML survives PyYAML turning it into an integer.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from watchdog.core.countries import known_country_codes
from watchdog.core.cpv import normalise_cpv
from watchdog.core.enums import ContractNature, NoticeStage

DEFAULT_CONFIG_PATH = Path("config/sources/ted.yaml")

# TED rejects a limit above this outright, with SEARCH_EXCEEDS_MAX_LIMIT.
MAX_PAGE_SIZE = 250

# TED also prices a request as "fields per page" and refuses one over this,
# with SEARCH_FIELDS_PER_PAGE_EXCEEDS_MAX_LIMIT. Measured on the live endpoint.
MAX_FIELDS_PER_PAGE = 10_000

# What one requested field name costs per notice, for the offline guard.
#
# This is a PRECAUTION, not a proven formula. Measured 13 September 2026, TED
# charged our own 55-name list exactly 55 per notice and accepted page size 181 -
# but a 40-name list built from `organisation-*-lot` names was charged 41, so at
# least one field name costs more than one and no arithmetic here can be trusted
# on its own. The extra unit buys a margin for that, and `watchdog config
# validate` confirms the real request against the live endpoint.
FIELD_COST = 2

# The largest page size the shipped field list fits in. config/sources/ted.yaml
# is the real setting; this is only what a config built without one gets, and it
# has to be a value that works rather than TED's bare maximum, which 55 fields
# have not fitted in since the projection was widened.
DEFAULT_PAGE_SIZE = 175


def fields_per_page(field_count: int, page_size: int) -> int:
    """What a request of this shape is assumed to cost TED, for the offline guard."""
    return (field_count + FIELD_COST) * page_size


def max_page_size(field_count: int) -> int:
    """The largest page size this many fields is allowed offline. May be 0."""
    return min(MAX_PAGE_SIZE, MAX_FIELDS_PER_PAGE // (field_count + FIELD_COST))


class ProvisionalCode(BaseModel):
    """A CPV code that is in the list on a stated bet rather than a measured gain.

    It is searched for exactly like any other code; nothing in the matcher or the
    query knows the difference. This exists so the recall audit can report on
    these codes by name instead of depending on someone rereading a comment.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    reason: str
    # When it became provisional, so an entry that has quietly outlived its
    # revisit is visible as a date rather than as a memory.
    since: date

    @field_validator("code", mode="before")
    @classmethod
    def _normalise_code(cls, value: Any) -> Any:
        return normalise_cpv(value) or value


class TedSourceConfig(BaseModel):
    """What to ask TED for. Every field is data from config/sources/ted.yaml."""

    model_config = ConfigDict(frozen=True)

    cpv_prefixes: list[str] = Field(default_factory=list)
    # Derived from the cpv_prefixes entries that carry a `provisional:` block, so
    # the two can never disagree about which codes exist.
    provisional_cpv: list[ProvisionalCode] = Field(default_factory=list)
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
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)

    @model_validator(mode="before")
    @classmethod
    def _split_provisional(cls, data: Any) -> Any:
        """Flatten the CPV list, keeping any `provisional:` block beside its code.

        An entry is either a bare code or a mapping with `code:` and an optional
        `provisional:`. Splitting it here means the query, the matcher and every
        caller keep seeing a plain list of codes.
        """
        if not isinstance(data, dict):
            return data

        entries = data.get("cpv_prefixes")
        if not isinstance(entries, list):
            return data

        codes: list[Any] = []
        provisional: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                codes.append(entry)
                continue

            if "code" not in entry:
                raise ValueError(
                    f"a CPV entry has no 'code:' key: {entry!r}; write it as a plain code, "
                    "or as 'code:' with an optional 'provisional:' block"
                )

            codes.append(entry["code"])
            note = entry.get("provisional")
            if note is not None:
                if not isinstance(note, dict):
                    raise ValueError(
                        f"'provisional:' on CPV {entry['code']} must give a 'reason:' and a "
                        "'since:' date, so the audit can report what the bet was"
                    )
                provisional.append({"code": entry["code"], **note})

        return {**data, "cpv_prefixes": codes, "provisional_cpv": provisional}

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
