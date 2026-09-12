"""Table definitions. Nothing here opens a connection or issues a query.

Dialect-neutral on purpose: the same definitions run on SQLite on a laptop and on
PostgreSQL when hosted. JSON, not JSONB. No SQLite-only types.

Enum values are stored as plain strings rather than database enum types, so adding
a vocabulary member stays a code change instead of a migration. The Pydantic models
in ``core.models`` validate the values on the way in and on the way out.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from watchdog.core.clock import ensure_utc

# Predictable constraint and index names, so a migration can always refer to one.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class UtcDateTime(TypeDecorator[datetime]):
    """Timestamps go in UTC-aware and come back UTC-aware on every backend.

    SQLite has no timezone type and hands back naive values; this puts UTC back on
    them so application code never sees a naive timestamp.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return ensure_utc(value)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TenderRow(Base):
    """A notice as received. Rows are updated and archived, never deleted."""

    __tablename__ = "tender"

    # "{source}:{source_id}" - deterministic, so the same notice keeps the same row.
    id: Mapped[str] = mapped_column(String(255), primary_key=True)

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(Text)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    title_language: Mapped[str | None] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(Text)

    buyer_name: Mapped[str | None] = mapped_column(Text)
    buyer_country: Mapped[str | None] = mapped_column(String(8))
    place_of_performance: Mapped[str | None] = mapped_column(Text)

    # A calendar date: the source states a day, not a moment.
    published_date: Mapped[date | None] = mapped_column(Date)
    deadline: Mapped[datetime | None] = mapped_column(UtcDateTime)

    notice_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    notice_subtype: Mapped[str | None] = mapped_column(String(64))
    contract_nature: Mapped[str] = mapped_column(String(16), nullable=False)

    cpv_main: Mapped[str | None] = mapped_column(String(16))
    cpv_additional: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    estimated_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    currency: Mapped[str | None] = mapped_column(String(8))

    documents_url: Mapped[str | None] = mapped_column(Text)
    languages: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    multi_lot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    first_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    # The untouched source payload, so any mapping can be re-checked later.
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_tender_source_source_id"),
        Index("ix_tender_published_date", "published_date"),
        Index("ix_tender_deadline", "deadline"),
        Index("ix_tender_buyer_country", "buyer_country"),
    )


class TenderChangeRow(Base):
    """One material change seen between two versions of a notice."""

    __tablename__ = "tender_change"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tender_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("tender.id"), nullable=False, index=True
    )
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    source_version: Mapped[str | None] = mapped_column(String(64))
    detected_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)


class ScreeningResultRow(Base):
    """Append-only. A new screening supersedes earlier rows; it never edits them."""

    __tablename__ = "screening_result"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tender_id: Mapped[str] = mapped_column(String(255), ForeignKey("tender.id"), nullable=False)

    score: Mapped[int] = mapped_column(Integer, nullable=False)
    band: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    rules: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    assessment: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)

    ai_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # sha256 of the exact title + description + CPV that was judged.
    screened_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    rules_version: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    profile_version: Mapped[str] = mapped_column(String(32), nullable=False)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    superseded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_screening_result_tender_id_created_at", "tender_id", text("created_at DESC")),
        Index("ix_screening_result_band_score", "band", "score"),
    )


class ReviewRow(Base):
    """A colleague's decision. Append-only, and no automated process may write it."""

    __tablename__ = "review"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tender_id: Mapped[str] = mapped_column(String(255), ForeignKey("tender.id"), nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    bid_route: Mapped[str] = mapped_column(String(16), nullable=False)
    owner: Mapped[str | None] = mapped_column(String(128))
    next_action: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    superseded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_review_tender_id_reviewed_at", "tender_id", text("reviewed_at DESC")),
    )


class TenderDetailRow(Base):
    """Extracted summary-template fields, each with its own source reference."""

    __tablename__ = "tender_detail"

    tender_id: Mapped[str] = mapped_column(String(255), ForeignKey("tender.id"), primary_key=True)
    fields: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    extracted_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(32))


class RunRow(Base):
    """One ingest or screening run, with its counts and errors."""

    __tablename__ = "run"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str | None] = mapped_column(String(32))
    window_from: Mapped[datetime | None] = mapped_column(UtcDateTime)
    window_to: Mapped[datetime | None] = mapped_column(UtcDateTime)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    counts: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False, default=dict)
    errors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    watermark_advanced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class WatermarkRow(Base):
    """How far each source has been read successfully. One row per source."""

    __tablename__ = "watermark"

    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_successful_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
