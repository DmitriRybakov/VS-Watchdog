"""Register filter columns, and the job lock the browser runs a pipeline through.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-13

Two unrelated things, in one revision because they arrive with the same feature.

**Filter columns on screening_result.** The register filters by domain, by which
keyword rule matched and by reason code. All three were only ever inside the
`rules` and `reason_codes` JSON, and a JSON column cannot be asked "does it
contain this value" in a way that works on SQLite and PostgreSQL both - so those
filters would have had to be done in Python, over the whole register, before
paging. They are stored in the same `,a,b,` delimited form the tender table
already uses for place_of_performance_country.

The `,` default is a placeholder meaning "nothing recorded", not a measurement.
Every existing row can be recomputed from the blob beside it, and the re-screen
that follows this migration does exactly that. Until it has run, the domain,
keyword and reason-code filters match nothing rather than matching wrongly.

**job_lock.** One row, holding both "is a run in progress" and how far it has
got, so the browser can start an ingest or a re-screen without a terminal and a
second press cannot start a second run. Deliberately one row and not a queue:
this is a triggered job, not a scheduler.

Self-contained on purpose: it uses only SQLAlchemy types, never application code.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The empty DelimitedList: a lone delimiter, so `LIKE '%,x,%'` cannot match it.
_EMPTY = ","


def upgrade() -> None:
    for column in ("domains", "matched_rules", "reason_tags"):
        op.add_column(
            "screening_result",
            sa.Column(column, sa.Text(), nullable=False, server_default=_EMPTY),
        )

    op.create_table(
        "job_lock",
        sa.Column("name", sa.String(length=32), primary_key=True),
        sa.Column("held", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("kind", sa.String(length=32), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("phase", sa.String(length=64), nullable=True),
        sa.Column("processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("job_lock")
    with op.batch_alter_table("screening_result") as batch:
        batch.drop_column("reason_tags")
        batch.drop_column("matched_rules")
        batch.drop_column("domains")
