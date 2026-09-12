"""The migration and the table definitions must describe the same database.

If they drift, a laptop built with `make migrate` and a hosted database built the
same way would disagree with the code that queries them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from watchdog.core.settings import get_settings
from watchdog.storage.tables import Base

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def migrated_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run `alembic upgrade head` against an empty SQLite file and return its URL."""
    url = f"sqlite:///{(tmp_path / 'migrated.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
        command.upgrade(config, "head")
    finally:
        get_settings.cache_clear()
    return url


def test_migration_creates_the_same_tables_and_columns(migrated_url: str) -> None:
    engine = create_engine(migrated_url)
    try:
        inspector = inspect(engine)
        migrated_tables = set(inspector.get_table_names()) - {"alembic_version"}

        assert migrated_tables == set(Base.metadata.tables)

        for name, table in Base.metadata.tables.items():
            migrated_columns = {column["name"] for column in inspector.get_columns(name)}
            assert migrated_columns == {column.name for column in table.columns}, name
    finally:
        engine.dispose()


def test_migration_creates_the_indexes_the_register_relies_on(migrated_url: str) -> None:
    engine = create_engine(migrated_url)
    try:
        inspector = inspect(engine)
        tender_indexes = {index["name"] for index in inspector.get_indexes("tender")}
        screening_indexes = {index["name"] for index in inspector.get_indexes("screening_result")}
        review_indexes = {index["name"] for index in inspector.get_indexes("review")}
    finally:
        engine.dispose()

    assert {
        "ix_tender_published_date",
        "ix_tender_deadline",
        "ix_tender_buyer_country",
    } <= tender_indexes
    assert {
        "ix_screening_result_tender_id_created_at",
        "ix_screening_result_band_score",
    } <= screening_indexes
    assert "ix_review_tender_id_reviewed_at" in review_indexes
