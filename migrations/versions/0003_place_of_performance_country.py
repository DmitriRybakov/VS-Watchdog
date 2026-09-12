"""Tender: where the work happens, at country level and separately from the buyer.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-12

`place_of_performance` already held TED's mixed NUTS-and-country codes as a
string, which cannot be filtered structurally. This adds the country level on its
own so the register can answer "work happening in Angola" without also answering
"bought by a French organisation". See docs/decisions/0003.

Self-contained on purpose: it uses only SQLAlchemy types, never application code.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Rows written before this column existed have no country recorded. An empty list
# is "we did not read it", which is what they deserve; re-mapping from `raw` fills
# them in without a guess.
_EMPTY_DELIMITED_LIST = ","


def upgrade() -> None:
    op.add_column(
        "tender",
        sa.Column(
            "place_of_performance_country",
            sa.Text(),
            nullable=False,
            server_default=_EMPTY_DELIMITED_LIST,
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("tender") as batch:
        batch.drop_column("place_of_performance_country")
