"""The migration and the table definitions must describe the same database.

If they drift, a laptop built with `make migrate` and a hosted database built the
same way would disagree with the code that queries them.

**A migration test must not be able to reach a real database.** One already did:
the fixture set ``WATCHDOG_DATABASE_URL`` while ``migrations/env.py`` reads
``DATABASE_URL``, so the test ran against whatever the environment already said -
the working register - and a downgrade dropped 2,029 ``documents_url`` values out
of it. A variable name that does not match must fail loudly, not fall back.

So every test here goes through :func:`disposable`, which asserts three things
before Alembic is allowed to run: the URL Alembic will actually resolve is the one
we meant, it is SQLite, and the file is under pytest's own temporary directory. A
mismatch is an assertion failure before a single statement is executed.
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

# The variable migrations/env.py actually reads, through core.settings. Named here
# so a rename there fails this test rather than silently pointing it somewhere else.
URL_VARIABLE = "DATABASE_URL"


def disposable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    *,
    variable: str = URL_VARIABLE,
) -> str:
    """Point Alembic at a throwaway SQLite file, and prove that it is one.

    Returns the URL only after checking that the settings object Alembic will
    build resolves to exactly it. ``variable`` exists so the guard can be tested
    with the wrong name, which is the mistake that destroyed real data once.
    """
    url = f"sqlite:///{(tmp_path / name).as_posix()}"

    monkeypatch.setenv(variable, url)
    get_settings.cache_clear()

    resolved = get_settings().database_url
    assert resolved == url, (
        f"the migration environment resolves to {resolved!r}, not the disposable database "
        f"{url!r}. Check that {URL_VARIABLE} is the variable migrations/env.py reads."
    )
    assert resolved.startswith("sqlite:"), "a migration test may only run against SQLite"
    # as_posix on both sides: the URL is built with forward slashes and a Windows
    # path is not, so comparing str(tmp_path) would reject every temporary file.
    assert tmp_path.as_posix() in resolved, (
        "a migration test may only run against a file under pytest's temporary directory"
    )

    return url


def alembic_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return config


@pytest.fixture
def migrated_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run `alembic upgrade head` against an empty SQLite file and return its URL."""
    url = disposable(tmp_path, monkeypatch, "migrated.db")
    try:
        command.upgrade(alembic_config(), "head")
    finally:
        get_settings.cache_clear()
    return url


def test_a_migration_test_refuses_to_run_against_anything_but_a_throwaway_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard itself, reproducing the mistake exactly as it happened.

    The old fixture set ``WATCHDOG_DATABASE_URL`` while migrations/env.py reads
    ``DATABASE_URL``, so Alembic ran against the working register and a downgrade
    dropped 2,029 document links out of it. Setting the wrong variable now fails
    before a single statement is executed.
    """
    monkeypatch.setenv(URL_VARIABLE, "sqlite:///data/watchdog.db")
    get_settings.cache_clear()

    try:
        with pytest.raises(AssertionError, match="disposable database"):
            disposable(tmp_path, monkeypatch, "right.db", variable="WATCHDOG_DATABASE_URL")
    finally:
        get_settings.cache_clear()


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
        config_indexes = {index["name"] for index in inspector.get_indexes("config_version")}
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
    assert "ix_config_version_kind_active" in config_indexes


def test_downgrading_one_step_and_upgrading_again_stays_on_the_throwaway_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A downgrade is the dangerous direction, so it is the one pinned to a temp file."""
    url = disposable(tmp_path, monkeypatch, "roundtrip.db")
    config = alembic_config()
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "-1")
        command.upgrade(config, "head")
    finally:
        get_settings.cache_clear()

    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()

    assert tables == set(Base.metadata.tables)
