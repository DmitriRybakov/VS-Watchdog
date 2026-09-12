"""Run: which provider, model and prompt version produced it, and how long it waited.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-12

A screening run that records only counts cannot be defended afterwards: the same
notices judged by a different model, or under a corrected prompt, give different
answers and the run itself is the only place that difference is visible. The three
version fields move independently of each other, so they are three columns rather
than one composed string.

Nullable on purpose. An ingest run calls no model, and "no model was involved" is
a different fact from "we do not know which".

Self-contained on purpose: it uses only SQLAlchemy types, never application code.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("run", sa.Column("provider", sa.String(32), nullable=True))
    op.add_column("run", sa.Column("model", sa.String(128), nullable=True))
    op.add_column("run", sa.Column("prompt_version", sa.String(32), nullable=True))
    op.add_column(
        "run",
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.drop_column("latency_ms")
        batch.drop_column("prompt_version")
        batch.drop_column("model")
        batch.drop_column("provider")
