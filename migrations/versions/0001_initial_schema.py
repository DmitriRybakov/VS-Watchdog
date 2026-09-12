"""Initial schema: tender, tender_change, screening_result, review, tender_detail, run, watermark.

Revision ID: 0001
Revises:
Create Date: 2026-09-12

Self-contained on purpose: it uses only SQLAlchemy types, never application code,
so this file still runs years after the models around it have moved on. Timestamp
columns are DateTime(timezone=True), which is what storage.tables.UtcDateTime
renders to.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tender",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("source_version", sa.String(length=64), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("title_language", sa.String(length=16), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("buyer_name", sa.Text(), nullable=True),
        sa.Column("buyer_country", sa.String(length=8), nullable=True),
        sa.Column("place_of_performance", sa.Text(), nullable=True),
        sa.Column("published_date", sa.Date(), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notice_stage", sa.String(length=32), nullable=False),
        sa.Column("notice_subtype", sa.String(length=64), nullable=True),
        sa.Column("contract_nature", sa.String(length=16), nullable=False),
        sa.Column("cpv_main", sa.String(length=16), nullable=True),
        sa.Column("cpv_additional", sa.JSON(), nullable=False),
        sa.Column("estimated_value", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("documents_url", sa.Text(), nullable=True),
        sa.Column("languages", sa.JSON(), nullable=False),
        sa.Column("multi_lot", sa.Boolean(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tender")),
        sa.UniqueConstraint("source", "source_id", name="uq_tender_source_source_id"),
    )
    op.create_index("ix_tender_published_date", "tender", ["published_date"], unique=False)
    op.create_index("ix_tender_deadline", "tender", ["deadline"], unique=False)
    op.create_index("ix_tender_buyer_country", "tender", ["buyer_country"], unique=False)

    op.create_table(
        "tender_change",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tender_id", sa.String(length=255), nullable=False),
        sa.Column("field", sa.String(length=64), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("source_version", sa.String(length=64), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tender_id"], ["tender.id"], name=op.f("fk_tender_change_tender_id_tender")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tender_change")),
    )
    op.create_index("ix_tender_change_tender_id", "tender_change", ["tender_id"], unique=False)

    op.create_table(
        "screening_result",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tender_id", sa.String(length=255), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("band", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("confidence_reasons", sa.JSON(), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("assessment", sa.JSON(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("ai_enabled", sa.Boolean(), nullable=False),
        sa.Column("screened_content_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column("rules_version", sa.String(length=32), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("profile_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tender_id"], ["tender.id"], name=op.f("fk_screening_result_tender_id_tender")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_screening_result")),
    )
    # Newest result per tender, and the register's band/score listing.
    op.create_index(
        "ix_screening_result_tender_id_created_at",
        "screening_result",
        ["tender_id", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_screening_result_band_score", "screening_result", ["band", "score"], unique=False
    )

    op.create_table(
        "review",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tender_id", sa.String(length=255), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("bid_route", sa.String(length=16), nullable=False),
        sa.Column("owner", sa.String(length=128), nullable=True),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.String(length=128), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tender_id"], ["tender.id"], name=op.f("fk_review_tender_id_tender")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review")),
    )
    # Newest review per tender.
    op.create_index(
        "ix_review_tender_id_reviewed_at",
        "review",
        ["tender_id", sa.text("reviewed_at DESC")],
        unique=False,
    )

    op.create_table(
        "tender_detail",
        sa.Column("tender_id", sa.String(length=255), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(
            ["tender_id"], ["tender.id"], name=op.f("fk_tender_detail_tender_id_tender")
        ),
        sa.PrimaryKeyConstraint("tender_id", name=op.f("pk_tender_detail")),
    )

    op.create_table(
        "run",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=True),
        sa.Column("window_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("watermark_advanced", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_run")),
    )

    op.create_table(
        "watermark",
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("last_successful_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("source", name=op.f("pk_watermark")),
    )


def downgrade() -> None:
    op.drop_table("watermark")
    op.drop_table("run")
    op.drop_table("tender_detail")
    op.drop_index("ix_review_tender_id_reviewed_at", table_name="review")
    op.drop_table("review")
    op.drop_index("ix_screening_result_band_score", table_name="screening_result")
    op.drop_index("ix_screening_result_tender_id_created_at", table_name="screening_result")
    op.drop_table("screening_result")
    op.drop_index("ix_tender_change_tender_id", table_name="tender_change")
    op.drop_table("tender_change")
    op.drop_index("ix_tender_buyer_country", table_name="tender")
    op.drop_index("ix_tender_deadline", table_name="tender")
    op.drop_index("ix_tender_published_date", table_name="tender")
    op.drop_table("tender")
