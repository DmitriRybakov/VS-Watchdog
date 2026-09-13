"""A database failure must name itself, and must never quote the notice.

The failure this is written against: the first hosted ingest reported only
"(raised as a result of Query-invoked autoflush; consider using a
session.no_autoflush block ...)", because the summary took the first line of the
exception and SQLAlchemy puts its notes there, ahead of the message.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import DataError, IntegrityError, OperationalError

from watchdog.storage.errors import describe_error

_STATEMENT = "INSERT INTO tender (id, deadline_source) VALUES (%(id)s, %(deadline_source)s)"
_SECRET = "deadline-receipt-tender-date-lot+deadline-receipt-tender-time-lot"


class _Diagnostic:
    """The shape psycopg exposes as `error.diag`."""

    def __init__(self, **fields: str | None) -> None:
        self.sqlstate = fields.get("sqlstate")
        self.table_name = fields.get("table_name")
        self.column_name = fields.get("column_name")
        self.constraint_name = fields.get("constraint_name")


class _DriverError(Exception):
    def __init__(self, message: str, diag: _Diagnostic) -> None:
        super().__init__(message)
        self.diag = diag


def _statement_error(kind: type, message: str, **diag: str | None) -> Any:
    error = kind(
        _STATEMENT,
        {"id": "ted:622963-2026", "deadline_source": _SECRET},
        _DriverError(message, _Diagnostic(**diag)),
    )
    error.add_detail(
        "raised as a result of Query-invoked autoflush; consider using a "
        "session.no_autoflush block if this flush is occurring prematurely"
    )
    return error


def test_a_value_too_long_says_which_type_and_which_table() -> None:
    error = _statement_error(
        DataError, "value too long for type character varying(64)", sqlstate="22001"
    )

    described = describe_error(error)

    assert "value too long for type character varying(64)" in described
    assert "table tender" in described
    assert "22001" in described


def test_a_constraint_violation_names_the_column_and_the_constraint() -> None:
    error = _statement_error(
        IntegrityError,
        'null value in column "title" of relation "tender" violates not-null constraint',
        sqlstate="23502",
        table_name="tender",
        column_name="title",
        constraint_name="ck_tender_title",
    )

    described = describe_error(error)

    assert "column title" in described
    assert "constraint ck_tender_title" in described


def test_the_autoflush_note_never_becomes_the_whole_message() -> None:
    error = _statement_error(
        DataError, "value too long for type character varying(64)", sqlstate="22001"
    )

    assert "autoflush" not in describe_error(error)


def test_the_payload_never_reaches_the_message() -> None:
    """Neither the bound values nor the value PostgreSQL quotes after a colon."""
    error = _statement_error(
        DataError, f'invalid input syntax for type integer: "{_SECRET}"', sqlstate="22P02"
    )

    described = describe_error(error)

    assert _SECRET not in described
    assert "VALUES" not in described
    assert "invalid input syntax for type integer" in described


def test_sqlite_keeps_the_column_names_it_puts_after_the_colon() -> None:
    """SQLite has no SQLSTATE and names the columns where PostgreSQL puts a value."""
    error = IntegrityError(
        _STATEMENT,
        {},
        Exception("UNIQUE constraint failed: tender.source, tender.source_id"),
    )

    assert "tender.source_id" in describe_error(error)


def test_any_other_error_is_still_one_line() -> None:
    error = OperationalError(_STATEMENT, {}, Exception("could not connect to server\nsecond line"))

    described = describe_error(error)

    assert described.endswith("could not connect to server")
    assert "second line" not in described
