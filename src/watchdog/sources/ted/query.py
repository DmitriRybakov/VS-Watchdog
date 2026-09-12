"""Building the TED expert query. One pure function, no network, no clock.

Three things this deliberately does not do.

**No Entr keywords.** TED titles arrive in every EU language and our vocabulary is
English. A keyword in the query would destroy recall. Keyword work happens
locally, on the original text, in the screening stage.

**No hour arithmetic.** ``publication-date`` accepts ``YYYYMMDD`` or
``today(+/-n)`` and nothing else - an ISO date is rejected with an explicit
pattern. The overlap is therefore whole days, and the config key says so.

**No leading-zero loss.** CPV codes are already eight-character strings by the
time they reach here; see watchdog.core.cpv.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from watchdog.sources.ted.config import TedSourceConfig
from watchdog.sources.ted.vocabulary import STAGE_TO_FORM_TYPE

# TED's own date format. Anything else is a 400 with QUERY_INVALID_FIELD_FORMAT.
DATE_FORMAT = "%Y%m%d"


class QueryProfile(StrEnum):
    """Which question we are asking TED."""

    # The daily run: our CPV codes, our stages, services only.
    DEFAULT = "default"
    # The monthly recall audit: same window and stages, no CPV filter and no
    # contract-nature filter, so we can measure what the daily query misses.
    AUDIT = "audit"


def format_window_date(value: date) -> str:
    return value.strftime(DATE_FORMAT)


def build_query(
    config: TedSourceConfig,
    window_from: date,
    window_to: date,
    *,
    profile: QueryProfile = QueryProfile.DEFAULT,
) -> str:
    """The expert query for one window.

    The window is inclusive at both ends and always present: without it the query
    would match the whole archive.
    """
    if window_to < window_from:
        raise ValueError(
            f"window_to {window_to.isoformat()} is before window_from {window_from.isoformat()}"
        )

    clauses: list[str] = []

    if profile is QueryProfile.DEFAULT and config.cpv_prefixes:
        # Hierarchical at TED's end: a parent code matches its descendants.
        clauses.append(f"classification-cpv IN ({' '.join(config.cpv_prefixes)})")

    if config.notice_stages:
        form_types = _form_types(config)
        if form_types:
            clauses.append(f"form-type IN ({' '.join(form_types)})")

    if profile is QueryProfile.DEFAULT and config.contract_natures:
        natures = [nature.value for nature in config.contract_natures]
        clauses.append(f"contract-nature IN ({' '.join(natures)})")

    if config.buyer_countries:
        clauses.append(f"buyer-country IN ({' '.join(config.buyer_countries)})")

    if config.place_of_performance:
        clauses.append(
            f"place-of-performance-country-proc IN ({' '.join(config.place_of_performance)})"
        )

    clauses.append(
        f"publication-date>={format_window_date(window_from)} "
        f"AND publication-date<={format_window_date(window_to)}"
    )

    if config.extra_query:
        clauses.append(config.extra_query.strip())

    return " AND ".join(f"({clause})" for clause in clauses)


def _form_types(config: TedSourceConfig) -> list[str]:
    """Configured stages as TED form-type values, deduplicated, order preserved."""
    seen: set[str] = set()
    form_types: list[str] = []
    for stage in config.notice_stages:
        form_type = STAGE_TO_FORM_TYPE.get(stage)
        if form_type is not None and form_type not in seen:
            seen.add(form_type)
            form_types.append(form_type)
    return form_types
