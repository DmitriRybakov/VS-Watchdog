"""Tender: which ingest run fetched it first, and which one saw it last.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-12

Without this, a row in the register cannot be tied to the run that produced it,
so a number on screen cannot be traced back to the window and the counts it came
from. Two columns rather than one, mirroring first_seen_at and last_seen_at:
the first is written once, the second moves on every sighting.

Deliberately nullable and without a foreign key. Rows written before this existed
have no run, and "we do not know" is the honest value for them; a run row is
never deleted, so the reference cannot dangle in practice.

Self-contained on purpose: it uses only SQLAlchemy types, never application code.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tender", sa.Column("first_seen_run_id", sa.String(64), nullable=True))
    op.add_column("tender", sa.Column("last_seen_run_id", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tender") as batch:
        batch.drop_column("last_seen_run_id")
        batch.drop_column("first_seen_run_id")
