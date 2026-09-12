"""Screening result: what the keyword rules alone claimed.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-13

A score a model reached and a score four keywords reached are different kinds of
claim. Holding both in one column would make "score 3" a question rather than an
answer, and no later query could separate them again.

Null on purpose. "A model judged this notice" is a different fact from "the rules
scored it 0", and an assessed result has no rules-only claim to record.

Self-contained on purpose: it uses only SQLAlchemy types, never application code.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("screening_result", sa.Column("rules_only_score", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("screening_result") as batch:
        batch.drop_column("rules_only_score")
