"""Quarantine: notices that could not be mapped, kept whole so they can be retried.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-12

Counting a mapping failure on the run told us it happened but threw away the
evidence: once the mapper was fixed, the notice was gone unless someone worked
out which window it had been in and fetched it again by hand. This keeps the
payload exactly as it arrived.

Self-contained on purpose: it uses only SQLAlchemy types, never application code.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quarantine",
        sa.Column("id", sa.String(255), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("error_type", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_quarantine"),
        sa.UniqueConstraint("source", "source_id", name="uq_quarantine_source_source_id"),
    )
    op.create_index(
        "ix_quarantine_resolved_last_seen_at",
        "quarantine",
        ["resolved", "last_seen_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_quarantine_resolved_last_seen_at", table_name="quarantine")
    op.drop_table("quarantine")
