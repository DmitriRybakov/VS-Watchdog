"""Configuration versions and interface feedback, both stored in the database.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-13

Two tables that exist for the same reason: on a host, anything written to a file
is erased by the next deploy and is invisible to a scheduled job running in a
different container. The active rule set, scoring policy and mandate move into
`config_version`; a colleague's notes about the interface go into
`feedback_comment`.

`config_version.version` is **imported from the YAML file, not reset to 1**. Ten
thousand stored screening results are stamped `rules_version = 3`; a fresh
numbering would make every one of them claim a rule set that never existed. The
seeding is done by the application, not here, because it has to read and validate
the YAML - a migration that parsed configuration would be a second copy of the
schema.

Self-contained: SQLAlchemy types only, never application code.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "config_version",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("saved_by", sa.String(length=128), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_config_version"),
        sa.UniqueConstraint("kind", "version", name="uq_config_version_kind_version"),
    )
    op.create_index("ix_config_version_kind_active", "config_version", ["kind", "active"])

    op.create_table(
        "feedback_comment",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("page", sa.Text(), nullable=False),
        sa.Column("element", sa.Text(), nullable=False),
        sa.Column("element_label", sa.Text(), nullable=True),
        sa.Column("tender_id", sa.String(length=255), nullable=True),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("reported_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_feedback_comment"),
    )
    op.create_index("ix_feedback_comment_page_element", "feedback_comment", ["page", "element"])


def downgrade() -> None:
    """Drops both tables, and with them every saved configuration version.

    Going back past this point means the active configuration is the YAML file
    again. Export first - `watchdog config export` writes the active versions back
    to config/*.yaml - or the history of who changed what is gone.
    """
    op.drop_index("ix_feedback_comment_page_element", table_name="feedback_comment")
    op.drop_table("feedback_comment")
    op.drop_index("ix_config_version_kind_active", table_name="config_version")
    op.drop_table("config_version")
