"""Screening result: the two evidence counts the register orders on.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-13

The register orders notices on four things: the rules priority, the strength of the
strongest domain rule that matched, how many distinct domain rules matched, and how
recently the notice was published. The middle two were only ever inside the `rules`
JSON blob, which cannot be ordered on with paging, so the order had to be done in
Python over the whole set.

Both are already recoverable from that blob on every existing row. The zero default
is a placeholder rather than a measurement, and the screening run that follows this
migration replaces it on every row - an ordering column that quietly reads zero for
older results would put them at the bottom of a view that looks correct.

Self-contained on purpose: it uses only SQLAlchemy types, never application code.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "screening_result",
        sa.Column("domain_strength_rank", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "screening_result",
        sa.Column("domain_rules_matched", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_screening_result_register_order",
        "screening_result",
        ["rules_only_score", "score", "domain_strength_rank", "domain_rules_matched"],
    )


def downgrade() -> None:
    op.drop_index("ix_screening_result_register_order", table_name="screening_result")
    with op.batch_alter_table("screening_result") as batch:
        batch.drop_column("domain_rules_matched")
        batch.drop_column("domain_strength_rank")
