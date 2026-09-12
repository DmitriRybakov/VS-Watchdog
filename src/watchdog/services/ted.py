"""TED operations the CLI and the web layer can call. Writes nothing to the database.

The configuration seed is read here, in the service layer, because business logic
must not read a config file directly. When the config_version table becomes the
active source, only this module changes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from watchdog.core.clock import utc_now
from watchdog.core.models import Tender
from watchdog.sources.errors import MappingError
from watchdog.sources.ted import (
    REQUESTED_FIELDS,
    TedClient,
    TedSource,
    TedSourceConfig,
    load_ted_config,
)
from watchdog.sources.ted.config import DEFAULT_CONFIG_PATH
from watchdog.sources.ted.query import QueryProfile, build_query


@dataclass(frozen=True)
class FieldCheck:
    """What `watchdog config validate` found."""

    requested: tuple[str, ...]
    unknown: tuple[str, ...]
    query: str
    query_valid: bool
    query_error: str | None = None

    @property
    def ok(self) -> bool:
        return not self.unknown and self.query_valid


@dataclass(frozen=True)
class ProbeRow:
    """One line of `watchdog ted-probe`. Nothing here is stored."""

    source_id: str
    title: str
    cpv_main: str | None
    cpv_all: tuple[str, ...]
    stage: str
    deadline: str
    deadline_type: str
    buyer_country: str | None
    performance_countries: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProbeResult:
    """What `watchdog ted-probe` found, and what it did not show.

    ``total`` is the whole window; ``rows`` is the part the limit allowed through.
    Keeping both is the point: a listing that silently stops at the limit reads as
    the complete answer, and a conclusion drawn from it is a conclusion about the
    start of the window rather than the window.
    """

    rows: tuple[ProbeRow, ...]
    total: int
    limit: int
    window_from: date
    window_to: date

    @property
    def truncated(self) -> bool:
        return self.total > len(self.rows)


def get_config(path: Path | str = DEFAULT_CONFIG_PATH) -> TedSourceConfig:
    return load_ted_config(path)


def window_for(days: int, *, today: date | None = None) -> tuple[date, date]:
    """The publication-date window covering ``days`` calendar days up to today.

    Inclusive at both ends, so ``days=7`` is seven days and ``days=1`` is today
    alone. TED compares with ``>=`` and ``<=``, so the end day counts as one.
    """
    end = today or utc_now().date()
    return (end - timedelta(days=max(1, days) - 1), end)


def build_probe_query(config: TedSourceConfig, days: int, *, today: date | None = None) -> str:
    window_from, window_to = window_for(days, today=today)
    return build_query(config, window_from, window_to)


def check_fields(
    config: TedSourceConfig,
    *,
    days: int = 1,
    client: TedClient | None = None,
) -> FieldCheck:
    """Check the field list and the query against the live endpoint.

    ``fields`` is mandatory at TED and one unrecognised name fails the whole
    request, so an untested list fails as an empty result set rather than as an
    error. This turns that into a named problem before a run depends on it.
    """
    window_from, window_to = window_for(days)
    owned = client is None
    ted = client or TedClient()

    try:
        unknown = ted.unknown_fields(REQUESTED_FIELDS, run_id=None)
        query = build_query(config, window_from, window_to)

        query_valid = True
        query_error: str | None = None
        if not unknown:
            try:
                ted.validate_query(query, REQUESTED_FIELDS)
            except Exception as exc:
                query_valid = False
                query_error = str(exc)

        return FieldCheck(
            requested=REQUESTED_FIELDS,
            unknown=tuple(unknown),
            query=query,
            query_valid=query_valid,
            query_error=query_error,
        )
    finally:
        if owned:
            ted.close()


def probe(
    config: TedSourceConfig,
    *,
    days: int = 3,
    limit: int = 25,
    client: TedClient | None = None,
) -> ProbeResult:
    """Call TED live and return what came back. Reads only; stores nothing.

    The count is asked for separately, before the rows, so that stopping at the
    limit can be reported rather than hidden. Rows arrive in TED's own result
    order, which is publication number ascending.

    Two ways the count and the listing can disagree, neither of them visible in
    the printed line. They are two requests a second apart, so a notice published
    between them shifts the total by one. And the count is what TED matched, while
    the rows are what we could map: a notice that fails mapping still produces a
    row here, which is what keeps the arithmetic honest. If that ever changes to
    dropping unmappable notices instead, this listing starts understating the
    window silently - the exact failure the count was added to prevent.
    """
    window_from, window_to = window_for(days)
    run_id = uuid.uuid4().hex
    owned = client is None
    ted = client or TedClient()

    try:
        source = TedSource(ted, config, run_id=run_id)
        total = ted.count_notices(
            source.query_for(window_from, window_to), REQUESTED_FIELDS, run_id=run_id
        )

        rows: list[ProbeRow] = []
        for raw in source.discover(window_from, window_to):
            if len(rows) >= limit:
                break
            try:
                rows.append(_to_row(source.to_tender(raw)))
            except MappingError as exc:
                rows.append(
                    ProbeRow(
                        source_id=exc.source_id or raw.source_id,
                        title=f"could not be mapped: {exc}",
                        cpv_main=None,
                        cpv_all=(),
                        stage="-",
                        deadline="-",
                        deadline_type="-",
                        buyer_country=None,
                    )
                )

        return ProbeResult(
            rows=tuple(rows),
            total=total,
            limit=limit,
            window_from=window_from,
            window_to=window_to,
        )
    finally:
        if owned:
            ted.close()


def _to_row(tender: Tender) -> ProbeRow:
    if tender.deadline is not None:
        deadline = tender.deadline.strftime("%Y-%m-%d %H:%M UTC")
    elif tender.deadline_date is not None:
        deadline = f"{tender.deadline_date.isoformat()} (time not specified)"
    else:
        deadline = "none"

    return ProbeRow(
        source_id=tender.source_id,
        title=tender.title_native or tender.title,
        cpv_main=tender.cpv_main,
        cpv_all=tuple(tender.cpv_all),
        stage=tender.notice_stage.value,
        deadline=deadline,
        deadline_type=tender.deadline_type.value,
        buyer_country=tender.buyer_country,
        performance_countries=tuple(tender.place_of_performance_country),
    )


__all__ = [
    "REQUESTED_FIELDS",
    "FieldCheck",
    "ProbeResult",
    "ProbeRow",
    "QueryProfile",
    "TedSourceConfig",
    "build_probe_query",
    "check_fields",
    "get_config",
    "probe",
    "window_for",
]
