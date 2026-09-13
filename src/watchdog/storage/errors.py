"""One sentence describing a database failure, with nothing from the payload in it.

``str(exc)`` on a SQLAlchemy error is the wrong thing to show and the wrong thing
to log. Its first line is often a note rather than the failure - "raised as a
result of Query-invoked autoflush" is the one we hit - and the lines after it are
the whole failed statement and every bound value, which here is the notice itself.
Taking only the first line therefore threw away the part that identifies the
failure and kept the part that does not.

What is kept for a constraint or a type violation: what was violated, and which
table, column and constraint the database named. What is never kept: the value.
PostgreSQL puts the offending value after a colon in its message and the rest of
it in DETAIL, so both are dropped.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.exc import DataError, IntegrityError, StatementError

# Identifier only - no literal can be captured by this.
_TARGET_TABLE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+\"?([A-Za-z_][A-Za-z0-9_]*)\"?",
    re.IGNORECASE,
)


def describe_error(exc: BaseException) -> str:
    """A database failure in one line, or the first line of any other error."""
    if isinstance(exc, DataError | IntegrityError):
        described = _describe_violation(exc)
        if described:
            return described
    return _first_line(str(exc)) or type(exc).__name__


def _describe_violation(exc: StatementError) -> str | None:
    """What the database said was wrong, and where, minus the value it said it about."""
    orig = exc.orig
    if orig is None:
        return None

    diag: Any = getattr(orig, "diag", None)
    sqlstate = _sqlstate(orig, diag)
    problem = _first_line(str(orig))

    if sqlstate and problem:
        # PostgreSQL states the offending value after a colon; the wording before
        # it is what was violated. SQLite has no SQLSTATE and puts the column
        # names there instead, which is why this only applies to one of them.
        problem = problem.split(": ", 1)[0].strip()

    where = [
        f"{label} {value}"
        for label, value in (
            ("table", _table(exc, diag)),
            ("column", getattr(diag, "column_name", None)),
            ("constraint", getattr(diag, "constraint_name", None)),
        )
        if value
    ]

    if not problem and not where:
        return None

    parts = [problem or type(orig).__name__]
    if where:
        parts.append(f"({', '.join(where)})")
    if sqlstate:
        parts.append(f"[SQLSTATE {sqlstate}]")
    return " ".join(parts)


def _sqlstate(orig: BaseException, diag: Any) -> str | None:
    """The five-character code, wherever this driver keeps it."""
    for value in (
        getattr(diag, "sqlstate", None),
        getattr(orig, "sqlstate", None),
        getattr(orig, "pgcode", None),
    ):
        if isinstance(value, str) and value:
            return value
    return None


def _table(exc: StatementError, diag: Any) -> str | None:
    """The table the database named, or the one the statement was written against.

    PostgreSQL names the table on a constraint violation but not on a value that
    is too long for its column, and knowing which table a write failed on is most
    of the answer when several are written in the same run.
    """
    named = getattr(diag, "table_name", None)
    if isinstance(named, str) and named:
        return named

    statement = exc.statement
    if not statement:
        return None
    match = _TARGET_TABLE.search(statement)
    return match.group(1) if match else None


def _first_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[0].strip() if lines else ""
