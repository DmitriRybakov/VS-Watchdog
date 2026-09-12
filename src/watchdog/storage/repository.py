"""The only module that opens a database session or issues a query.

Every method takes and returns the plain models from ``core.models``; nothing
outside this file sees a SQLAlchemy row. The session factory is injected, so tests
run against an in-memory database with no other change.

Two rules this file exists to keep:

- Nothing is ever deleted. A notice that vanished from the source keeps its row.
- A screening result is append-only. A new result marks the previous one
  superseded; it never edits it, and it never touches a human review.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Select, and_, func, nulls_last, or_, select, update
from sqlalchemy.orm import InstrumentedAttribute, Session

from watchdog.core.clock import utc_now
from watchdog.core.enums import RunKind, RunStatus, SourcePlatform
from watchdog.core.models import (
    Review,
    Run,
    ScreeningResult,
    ScreeningVersions,
    Tender,
    TenderChange,
    TenderDetail,
    TenderFilters,
    TenderPage,
    TextBlock,
    UpsertStats,
    Watermark,
    content_hash,
    screening_text_from_blocks,
)
from watchdog.storage.db import SessionFactory
from watchdog.storage.tables import (
    ReviewRow,
    RunRow,
    ScreeningResultRow,
    TenderChangeRow,
    TenderDetailRow,
    TenderRow,
    WatermarkRow,
    contains_token,
)

# Everything on a tender that comes from the source. first_seen_at and
# last_seen_at are ours, and the id is derived, so none of them appear here.
SOURCE_FIELDS: tuple[str, ...] = (
    "source",
    "source_id",
    "source_version",
    "source_url",
    "title",
    "title_language",
    "title_native",
    "title_native_language",
    "description",
    "buyer_name",
    "buyer_country",
    "place_of_performance",
    "place_of_performance_country",
    "published_date",
    "deadline",
    "deadline_date",
    "deadline_source",
    "deadline_type",
    "notice_stage",
    "notice_subtype",
    "contract_nature",
    "contract_natures",
    "cpv_main",
    "cpv_additional",
    "cpv_all",
    "estimated_value",
    "currency",
    "documents_url",
    "languages",
    "multi_lot",
    "screening_blocks",
    "raw",
)

# Changes a colleague needs to be told about; the rest are updated silently.
MATERIAL_CHANGE_FIELDS: tuple[str, ...] = (
    "title",
    "title_native",
    "deadline",
    "deadline_date",
    "notice_stage",
    "source_version",
)

# Anything not on this list is rejected, so a sort key can never reach SQL.
SORT_KEYS: dict[str, InstrumentedAttribute[Any]] = {
    "published_date": TenderRow.published_date,
    "deadline": TenderRow.deadline,
    # Sorting the register by urgency uses the date, which is set even when the
    # source gave no time of day.
    "deadline_date": TenderRow.deadline_date,
    "first_seen_at": TenderRow.first_seen_at,
    "last_seen_at": TenderRow.last_seen_at,
    "title": TenderRow.title,
    "buyer_country": TenderRow.buyer_country,
    "score": ScreeningResultRow.score,
}

MAX_PAGE_SIZE = 200


class Repository:
    """Application queries. Construct it with a session factory."""

    def __init__(self, session_factory: SessionFactory | Callable[[], Session]) -> None:
        self._session_factory = session_factory

    # ------------------------------------------------------------------ tenders

    def upsert_tenders(self, tenders: Sequence[Tender]) -> UpsertStats:
        """Insert new notices and update known ones. Never deletes, never re-dates.

        ``first_seen_at`` is written once and never touched again. ``last_seen_at``
        moves on every sighting, even when nothing else changed, so "still open at
        the source" and "changed" stay separate facts.
        """
        stats = UpsertStats()

        with self._session_factory() as session:
            for tender in tenders:
                row = session.get(TenderRow, tender.id)

                if row is None:
                    session.add(_new_row(tender))
                    stats.new += 1
                    continue

                changed = _changed_fields(row, tender)
                for field in changed:
                    if field in MATERIAL_CHANGE_FIELDS:
                        session.add(
                            TenderChangeRow(
                                tender_id=row.id,
                                field=field,
                                old_value=_as_text(getattr(row, field)),
                                new_value=_as_text(getattr(tender, field)),
                                source_version=tender.source_version,
                                detected_at=utc_now(),
                            )
                        )

                _apply_source_fields(row, tender)
                row.last_seen_at = tender.last_seen_at

                if changed:
                    stats.updated += 1
                else:
                    stats.unchanged += 1

            session.commit()

        return stats

    def get_tender(self, tender_id: str) -> Tender | None:
        with self._session_factory() as session:
            row = session.get(TenderRow, tender_id)
            return Tender.model_validate(row) if row is not None else None

    def list_tenders(
        self,
        filters: TenderFilters | None = None,
        *,
        sort_by: str = "published_date",
        descending: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> TenderPage:
        """One page of the register, with the total the filters matched.

        ``sort_by`` must be one of ``SORT_KEYS``; anything else is a ValueError.
        ``limit`` is capped at ``MAX_PAGE_SIZE`` rather than refused, so a stray
        request cannot pull the whole register.
        """
        if sort_by not in SORT_KEYS:
            raise ValueError(
                f"unknown sort key {sort_by!r}; allowed: {', '.join(sorted(SORT_KEYS))}"
            )

        limit = max(1, min(limit, MAX_PAGE_SIZE))
        offset = max(0, offset)
        conditions = _filter_conditions(filters or TenderFilters())

        with self._session_factory() as session:
            total = session.scalar(
                _latest_screening_join(select(func.count()).select_from(TenderRow)).where(
                    *conditions
                )
            )

            order_column = SORT_KEYS[sort_by]
            ordering = order_column.desc() if descending else order_column.asc()

            rows = (
                session.execute(
                    _latest_screening_join(select(TenderRow))
                    .where(*conditions)
                    # Unknown values sort last either way, and id breaks ties so
                    # paging cannot show the same notice twice.
                    .order_by(nulls_last(ordering), TenderRow.id.asc())
                    .limit(limit)
                    .offset(offset)
                )
                .scalars()
                .all()
            )

            return TenderPage(
                items=[Tender.model_validate(row) for row in rows],
                total=total or 0,
                limit=limit,
                offset=offset,
            )

    def list_changes(self, tender_id: str) -> list[TenderChange]:
        """The change history of one notice, oldest first."""
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(TenderChangeRow)
                    .where(TenderChangeRow.tender_id == tender_id)
                    .order_by(TenderChangeRow.detected_at.asc(), TenderChangeRow.id.asc())
                )
                .scalars()
                .all()
            )
            return [TenderChange.model_validate(row) for row in rows]

    # ---------------------------------------------------------------- screening

    def latest_screening_for(self, ids: Iterable[str]) -> dict[str, ScreeningResult]:
        """The current result for each id. Tenders never screened are absent."""
        tender_ids = list(ids)
        if not tender_ids:
            return {}

        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(ScreeningResultRow).where(
                        ScreeningResultRow.tender_id.in_(tender_ids),
                        ScreeningResultRow.superseded.is_(False),
                    )
                )
                .scalars()
                .all()
            )
            return {row.tender_id: ScreeningResult.model_validate(row) for row in rows}

    def save_screening_result(self, result: ScreeningResult) -> ScreeningResult:
        """Append a result and supersede the previous one. Earlier rows keep their values."""
        with self._session_factory() as session:
            session.execute(
                update(ScreeningResultRow)
                .where(
                    ScreeningResultRow.tender_id == result.tender_id,
                    ScreeningResultRow.superseded.is_(False),
                )
                .values(superseded=True)
            )

            row = ScreeningResultRow(
                tender_id=result.tender_id,
                score=result.score,
                band=result.band.value,
                confidence=result.confidence,
                confidence_reasons=list(result.confidence_reasons),
                reason_codes=list(result.reason_codes),
                rules=result.rules.model_dump(mode="json"),
                assessment=(
                    result.assessment.model_dump(mode="json")
                    if result.assessment is not None
                    else None
                ),
                explanation=result.explanation,
                ai_enabled=result.ai_enabled,
                screened_content_hash=result.screened_content_hash,
                provider=result.provider,
                model=result.model,
                prompt_version=result.prompt_version,
                rules_version=result.rules_version,
                policy_version=result.policy_version,
                profile_version=result.profile_version,
                created_at=result.created_at,
                superseded=False,
            )
            session.add(row)
            session.commit()

            return ScreeningResult.model_validate(row)

    def screening_history(self, tender_id: str) -> list[ScreeningResult]:
        """Every screening of one tender, newest first, superseded ones included."""
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(ScreeningResultRow)
                    .where(ScreeningResultRow.tender_id == tender_id)
                    .order_by(ScreeningResultRow.created_at.desc(), ScreeningResultRow.id.desc())
                )
                .scalars()
                .all()
            )
            return [ScreeningResult.model_validate(row) for row in rows]

    def ids_needing_screening(
        self,
        current_versions: ScreeningVersions,
        current_provider: str,
        current_model: str | None,
    ) -> list[str]:
        """Tenders whose current result no longer reflects how we screen today.

        A tender needs re-screening when it has never been screened, or when any of
        these differ from its latest result: the content hash of its current
        screening text and CPV codes; the rules, policy, profile or prompt version;
        the provider or the model. Turning the model on therefore makes every
        rules-only result stale, which is what we want.
        """
        with self._session_factory() as session:
            latest = {
                row.tender_id: row
                for row in session.execute(
                    select(
                        ScreeningResultRow.tender_id,
                        ScreeningResultRow.screened_content_hash,
                        ScreeningResultRow.rules_version,
                        ScreeningResultRow.policy_version,
                        ScreeningResultRow.profile_version,
                        ScreeningResultRow.prompt_version,
                        ScreeningResultRow.provider,
                        ScreeningResultRow.model,
                    ).where(ScreeningResultRow.superseded.is_(False))
                )
            }

            stale: list[str] = []
            for tender in session.execute(
                select(
                    TenderRow.id,
                    TenderRow.screening_blocks,
                    TenderRow.cpv_all,
                ).order_by(TenderRow.id.asc())
            ):
                result = latest.get(tender.id)
                if result is None:
                    stale.append(tender.id)
                    continue

                blocks = [
                    TextBlock.model_validate(block) for block in tender.screening_blocks or []
                ]
                current_hash = content_hash(
                    screening_text_from_blocks(blocks),
                    tender.cpv_all or [],
                )
                if (
                    result.screened_content_hash != current_hash
                    or result.rules_version != current_versions.rules_version
                    or result.policy_version != current_versions.policy_version
                    or result.profile_version != current_versions.profile_version
                    or result.prompt_version != current_versions.prompt_version
                    or result.provider != current_provider
                    or result.model != current_model
                ):
                    stale.append(tender.id)

            return stale

    # ------------------------------------------------------------------ reviews

    def save_review(self, review: Review) -> Review:
        """Record a colleague's decision. Only ever called from a human action.

        Append-only: the previous review is marked superseded and keeps every word
        of what it said, because why someone changed their mind is worth keeping.
        """
        with self._session_factory() as session:
            session.execute(
                update(ReviewRow)
                .where(
                    ReviewRow.tender_id == review.tender_id,
                    ReviewRow.superseded.is_(False),
                )
                .values(superseded=True)
            )

            row = ReviewRow(
                tender_id=review.tender_id,
                verdict=review.verdict.value,
                bid_route=review.bid_route.value,
                owner=review.owner,
                next_action=review.next_action,
                note=review.note,
                reviewed_by=review.reviewed_by,
                reviewed_at=review.reviewed_at,
                superseded=False,
            )
            session.add(row)
            session.commit()

            return Review.model_validate(row)

    def get_review(self, tender_id: str) -> Review | None:
        """The current review, or None if nobody has decided yet."""
        with self._session_factory() as session:
            row = session.execute(
                select(ReviewRow).where(
                    ReviewRow.tender_id == tender_id,
                    ReviewRow.superseded.is_(False),
                )
            ).scalar_one_or_none()
            return Review.model_validate(row) if row is not None else None

    def list_reviews(self, tender_id: str) -> list[Review]:
        """Every review of one tender, newest first, superseded ones included."""
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(ReviewRow)
                    .where(ReviewRow.tender_id == tender_id)
                    .order_by(ReviewRow.reviewed_at.desc(), ReviewRow.id.desc())
                )
                .scalars()
                .all()
            )
            return [Review.model_validate(row) for row in rows]

    # ------------------------------------------------------------------ details

    def save_detail(self, detail: TenderDetail) -> TenderDetail:
        with self._session_factory() as session:
            row = session.get(TenderDetailRow, detail.tender_id)
            if row is None:
                row = TenderDetailRow(tender_id=detail.tender_id)
                session.add(row)

            row.fields = {
                name: field.model_dump(mode="json") for name, field in detail.fields.items()
            }
            row.extracted_at = detail.extracted_at
            row.model = detail.model
            row.prompt_version = detail.prompt_version

            session.commit()
            return TenderDetail.model_validate(row)

    def get_detail(self, tender_id: str) -> TenderDetail | None:
        with self._session_factory() as session:
            row = session.get(TenderDetailRow, tender_id)
            return TenderDetail.model_validate(row) if row is not None else None

    # --------------------------------------------------------------------- runs

    def start_run(
        self,
        kind: RunKind,
        *,
        source: SourcePlatform | None = None,
        window_from: datetime | None = None,
        window_to: datetime | None = None,
    ) -> Run:
        run = Run(
            run_id=uuid.uuid4().hex,
            kind=kind,
            source=source,
            window_from=window_from,
            window_to=window_to,
            started_at=utc_now(),
            status=RunStatus.RUNNING,
        )

        with self._session_factory() as session:
            session.add(
                RunRow(
                    run_id=run.run_id,
                    kind=run.kind.value,
                    source=run.source.value if run.source is not None else None,
                    window_from=run.window_from,
                    window_to=run.window_to,
                    started_at=run.started_at,
                    status=run.status.value,
                    counts={},
                    errors=[],
                    tokens_in=0,
                    tokens_out=0,
                    watermark_advanced=False,
                )
            )
            session.commit()

        return run

    def finish_run(
        self,
        run_id: str,
        *,
        status: RunStatus,
        counts: dict[str, int] | None = None,
        errors: Sequence[str] | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        watermark_advanced: bool = False,
    ) -> Run:
        with self._session_factory() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                raise ValueError(f"unknown run_id {run_id!r}")

            row.status = status.value
            row.counts = dict(counts or {})
            row.errors = list(errors or [])
            row.tokens_in = tokens_in
            row.tokens_out = tokens_out
            row.watermark_advanced = watermark_advanced
            row.finished_at = utc_now()

            session.commit()
            return Run.model_validate(row)

    def get_run(self, run_id: str) -> Run | None:
        with self._session_factory() as session:
            row = session.get(RunRow, run_id)
            return Run.model_validate(row) if row is not None else None

    def list_runs(self, *, limit: int = 20) -> list[Run]:
        """The most recent runs, newest first."""
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(RunRow).order_by(RunRow.started_at.desc()).limit(max(1, limit))
                )
                .scalars()
                .all()
            )
            return [Run.model_validate(row) for row in rows]

    # --------------------------------------------------------------- watermarks

    def get_watermark(self, source: SourcePlatform) -> Watermark | None:
        with self._session_factory() as session:
            row = session.get(WatermarkRow, source.value)
            return Watermark.model_validate(row) if row is not None else None

    def set_watermark(self, source: SourcePlatform, last_successful_at: datetime) -> Watermark:
        with self._session_factory() as session:
            row = session.get(WatermarkRow, source.value)
            if row is None:
                row = WatermarkRow(source=source.value)
                session.add(row)

            row.last_successful_at = last_successful_at
            row.updated_at = utc_now()

            session.commit()
            return Watermark.model_validate(row)


# ------------------------------------------------------------------- internals


def _latest_screening_on() -> Any:
    """Join condition: a tender and its current screening result, if it has one."""
    return and_(
        ScreeningResultRow.tender_id == TenderRow.id,
        ScreeningResultRow.superseded.is_(False),
    )


def _latest_screening_join(stmt: Select[Any]) -> Select[Any]:
    return stmt.join(ScreeningResultRow, _latest_screening_on(), isouter=True)


def _filter_conditions(filters: TenderFilters) -> list[Any]:
    conditions: list[Any] = []

    if filters.source is not None:
        conditions.append(TenderRow.source == filters.source.value)
    if filters.buyer_country is not None:
        conditions.append(TenderRow.buyer_country == filters.buyer_country)
    if filters.place_of_performance_country is not None:
        # Where the work happens, which is a different question from who is buying.
        conditions.append(
            contains_token(
                TenderRow.place_of_performance_country,
                filters.place_of_performance_country,
            )
        )
    if filters.notice_stage is not None:
        conditions.append(TenderRow.notice_stage == filters.notice_stage.value)
    if filters.contract_nature is not None:
        # The list, never the scalar: a works contract with a services component
        # has to be found by a services filter, and TED's own filter keeps it.
        conditions.append(contains_token(TenderRow.contract_natures, filters.contract_nature.value))
    if filters.published_from is not None:
        conditions.append(TenderRow.published_date >= filters.published_from)
    if filters.published_to is not None:
        conditions.append(TenderRow.published_date <= filters.published_to)
    if filters.deadline_from is not None:
        conditions.append(TenderRow.deadline >= filters.deadline_from)
    if filters.deadline_to is not None:
        conditions.append(TenderRow.deadline <= filters.deadline_to)
    if filters.band is not None:
        conditions.append(ScreeningResultRow.band == filters.band.value)
    if filters.min_score is not None:
        conditions.append(ScreeningResultRow.score >= filters.min_score)
    if filters.text:
        pattern = f"%{_escape_like(filters.text)}%"
        conditions.append(
            or_(
                TenderRow.title.ilike(pattern, escape="\\"),
                TenderRow.title_native.ilike(pattern, escape="\\"),
                TenderRow.description.ilike(pattern, escape="\\"),
            )
        )

    return conditions


def _escape_like(value: str) -> str:
    """A % typed into the search box means a percent sign, not "match anything"."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _new_row(tender: Tender) -> TenderRow:
    row = TenderRow(id=tender.id)
    _apply_source_fields(row, tender)
    row.first_seen_at = tender.first_seen_at
    row.last_seen_at = tender.last_seen_at
    return row


def _apply_source_fields(row: TenderRow, tender: Tender) -> None:
    for field in SOURCE_FIELDS:
        setattr(row, field, _column_value(getattr(tender, field)))


def _changed_fields(row: TenderRow, tender: Tender) -> list[str]:
    return [
        field
        for field in SOURCE_FIELDS
        if getattr(row, field) != _column_value(getattr(tender, field))
    ]


def _column_value(value: Any) -> Any:
    """Store an enum as its value, so the database never holds a Python repr."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_column_value(item) for item in value]
    return value


def _as_text(value: Any) -> str | None:
    """Render an old or new value for the change log. None stays None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
