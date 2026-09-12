"""Tender: native title, deadline meaning, every CPV code, every contract nature, screening blocks.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-12

Why each column exists is in docs/TED_API_CONTRACT.md and docs/decisions/0002.

Self-contained on purpose: it uses only SQLAlchemy types, never application code,
so this file still runs years after the models around it have moved on.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Existing rows predate these facts, so they get the "we do not know" value rather
# than a made-up one. New rows always carry a real value from the mapper.
_UNKNOWN_DEADLINE_TYPE = "unknown"
_EMPTY_DELIMITED_LIST = ","
_EMPTY_JSON_LIST = "[]"


def upgrade() -> None:
    op.add_column("tender", sa.Column("title_native", sa.Text(), nullable=True))
    op.add_column("tender", sa.Column("title_native_language", sa.String(length=16), nullable=True))

    op.add_column("tender", sa.Column("deadline_date", sa.Date(), nullable=True))
    op.add_column("tender", sa.Column("deadline_source", sa.String(length=64), nullable=True))
    op.add_column(
        "tender",
        sa.Column(
            "deadline_type",
            sa.String(length=32),
            nullable=False,
            server_default=_UNKNOWN_DEADLINE_TYPE,
        ),
    )

    # A delimited string, not JSON: "does this list contain services" has to work
    # the same on SQLite and PostgreSQL.
    op.add_column(
        "tender",
        sa.Column(
            "contract_natures", sa.Text(), nullable=False, server_default=_EMPTY_DELIMITED_LIST
        ),
    )

    op.add_column(
        "tender",
        sa.Column("cpv_all", sa.JSON(), nullable=False, server_default=_EMPTY_JSON_LIST),
    )
    op.add_column(
        "tender",
        sa.Column("screening_blocks", sa.JSON(), nullable=False, server_default=_EMPTY_JSON_LIST),
    )

    op.create_index("ix_tender_deadline_date", "tender", ["deadline_date"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tender_deadline_date", table_name="tender")
    with op.batch_alter_table("tender") as batch:
        batch.drop_column("screening_blocks")
        batch.drop_column("cpv_all")
        batch.drop_column("contract_natures")
        batch.drop_column("deadline_type")
        batch.drop_column("deadline_source")
        batch.drop_column("deadline_date")
        batch.drop_column("title_native_language")
        batch.drop_column("title_native")
