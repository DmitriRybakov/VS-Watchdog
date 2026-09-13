"""tender.deadline_source holds two field names, not a code, so it is Text.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-13

`deadline_source` records which TED fields a deadline was read from, as
"<date field>+<time field>". The shortest of those pairs is 65 characters and the
longest is 75, so every notice with a deadline overflowed the String(64) the
column was created with in 0002. SQLite ignores a declared length and stored them
anyway; PostgreSQL raised a DataError and lost the whole batch, which is why the
first hosted ingest wrote nothing.

Text rather than a wider varchar: the value is a composed provenance label, not a
code from a closed vocabulary, and a fourth deadline field with a longer name
would silently reintroduce the same failure. The column is not indexed and is
never compared against, so there is nothing to be gained from a bound.

Self-contained on purpose: it uses only SQLAlchemy types, never application code.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tender") as batch:
        batch.alter_column(
            "deadline_source",
            existing_type=sa.String(length=64),
            type_=sa.Text(),
            existing_nullable=True,
        )


def downgrade() -> None:
    # Any value written since the upgrade is longer than 64 characters, so going
    # back would truncate it. The USING clause makes that explicit rather than
    # letting PostgreSQL refuse the cast.
    with op.batch_alter_table("tender") as batch:
        batch.alter_column(
            "deadline_source",
            existing_type=sa.Text(),
            type_=sa.String(length=64),
            existing_nullable=True,
            postgresql_using="left(deadline_source, 64)",
        )
