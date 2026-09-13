"""The wider TED projection: procedure-level and across-lots facts.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-13

TED's search API was being asked for 37 of its 1,830 fields, and the fields cap
was the reason. Measuring the cap properly freed room for 18 more, which is where
the real language requirement, the award and selection criteria, the duration,
the procedure type, the bidding portal and the buyer's sector come from.

**Nothing here is attributed to a lot, and the columns are named so that it
cannot start being.** The search API flattens every lot-scoped field into one
array and guarantees no ordering, so a value can be reported as belonging to the
notice but never to a particular lot. `lot_ids` is the one exception: it is TED's
own list of lot identifiers and is the only authoritative statement of how many
lots exist. See docs/decisions/0008.

`documents_url` becomes `document_urls`: the old column held only the first link
of an array that is usually longer.

Existing rows get empty defaults. They are filled by re-ingesting, not by a
migration - these are facts TED was never asked for, so there is nothing in the
database to derive them from.

Self-contained on purpose: it uses only SQLAlchemy types, never application code.
"""

import json
from collections.abc import Callable, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSON columns that hold a list. Server default so the NOT NULL can be added
# without rewriting every row by hand.
_JSON_LISTS: tuple[str, ...] = (
    "lot_values",
    "document_urls",
    "lot_ids",
    "performance_cities",
    "submission_languages",
    "submission_urls",
    "framework_agreements",
    "dps_usages",
    "contract_durations",
    "contract_start_dates",
    "renewal_maximums",
    "award_criteria",
    "selection_criteria",
)


def upgrade() -> None:
    with op.batch_alter_table("tender") as batch:
        for name in _JSON_LISTS:
            batch.add_column(
                sa.Column(name, sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
            )
        batch.add_column(sa.Column("estimated_value_source", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("lot_value_currency", sa.String(length=8), nullable=True))
        batch.add_column(sa.Column("procedure_type", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("main_activity", sa.String(length=32), nullable=True))
        batch.add_column(
            sa.Column(
                "criteria_unpaired",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    # The single link every existing row already has, so nothing on screen goes
    # blank between this migration and the re-ingest that fills the rest.
    # Encoded with json.dumps rather than string concatenation: a URL containing a
    # quote would otherwise produce a column that no longer parses as JSON.
    _move(
        read="SELECT id, documents_url FROM tender "
        "WHERE documents_url IS NOT NULL AND documents_url <> ''",
        write="UPDATE tender SET document_urls = :value WHERE id = :id",
        encode=lambda value: json.dumps([value]),
    )

    with op.batch_alter_table("tender") as batch:
        batch.drop_column("documents_url")


def downgrade() -> None:
    with op.batch_alter_table("tender") as batch:
        batch.add_column(sa.Column("documents_url", sa.Text(), nullable=True))

    # Put the first link back. Without this the downgrade is lossy in a way that
    # only shows up after the next upgrade, as an empty column that looks as
    # though the source never sent a link.
    _move(
        read="SELECT id, document_urls FROM tender WHERE document_urls IS NOT NULL",
        write="UPDATE tender SET documents_url = :value WHERE id = :id",
        encode=_first_url,
    )

    with op.batch_alter_table("tender") as batch:
        batch.drop_column("criteria_unpaired")
        batch.drop_column("main_activity")
        batch.drop_column("procedure_type")
        batch.drop_column("lot_value_currency")
        batch.drop_column("estimated_value_source")
        for name in reversed(_JSON_LISTS):
            batch.drop_column(name)


def _move(*, read: str, write: str, encode: Callable[[Any], str | None]) -> None:
    """Copy one column into another, row by row, through Python.

    Row by row rather than in SQL because SQLite and PostgreSQL disagree about
    JSON functions, and a migration that works on a laptop and not when hosted is
    the failure this project has already had once.
    """
    connection = op.get_bind()
    for row in connection.execute(sa.text(read)).mappings().all():
        identifier, value = tuple(row.values())
        encoded = encode(value)
        if encoded is not None:
            connection.execute(sa.text(write), {"id": identifier, "value": encoded})


def _first_url(value: Any) -> str | None:
    """The first entry of a JSON list column, whether the driver decoded it or not."""
    items = value if isinstance(value, list) else json.loads(value or "[]")
    return items[0] if items else None
