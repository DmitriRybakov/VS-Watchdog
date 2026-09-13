"""What a provider's connection URL has to become before it can be used.

Two corrections, and both of them are silent failures if they are left undone: a
driver that is not installed, and a connection that is allowed to be
unencrypted. Neither shows up as an error on screen - the first fails at startup
with a message about psycopg2, and the second does not fail at all.
"""

from __future__ import annotations

import pytest

from watchdog.storage.db import create_db_engine, normalise_database_url

SUPABASE = (
    "postgresql://postgres.abcdef:secret@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"
)


def test_sqlite_is_left_exactly_as_it_was() -> None:
    assert normalise_database_url("sqlite:///data/watchdog.db") == "sqlite:///data/watchdog.db"
    assert normalise_database_url("sqlite+pysqlite:///:memory:") == "sqlite+pysqlite:///:memory:"


@pytest.mark.parametrize("prefix", ["postgres://", "postgresql://"])
def test_both_forms_a_provider_prints_become_the_driver_we_install(prefix: str) -> None:
    url = normalise_database_url(f"{prefix}user:pass@host:5432/db")

    assert url.startswith("postgresql+psycopg://")


def test_a_supabase_pooler_url_gets_encrypted_transport() -> None:
    url = normalise_database_url(SUPABASE)

    assert url.startswith("postgresql+psycopg://")
    assert url.endswith("?sslmode=require")


def test_an_existing_query_string_is_added_to_rather_than_replaced() -> None:
    url = normalise_database_url("postgresql://u:p@host/db?application_name=watchdog")

    assert "application_name=watchdog" in url
    assert "&sslmode=require" in url


def test_a_stated_ssl_mode_is_left_alone() -> None:
    url = normalise_database_url("postgresql://u:p@host/db?sslmode=verify-full")

    assert url.count("sslmode") == 1
    assert "verify-full" in url


def test_an_engine_for_a_sleeping_host_checks_a_connection_before_using_it() -> None:
    """A pooled connection that was fine before the service slept is not fine after."""
    engine = create_db_engine(SUPABASE)

    try:
        assert engine.pool._pre_ping is True
        assert engine.dialect.driver == "psycopg"
    finally:
        engine.dispose()
