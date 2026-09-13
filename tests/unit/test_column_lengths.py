"""What TED actually sends must fit the columns it is stored in.

SQLite ignores a declared string length and PostgreSQL does not, so a column that
is too narrow works on a laptop and loses a whole batch when hosted. That is how
`deadline_source` - two field names joined by "+", 65 to 75 characters, declared
String(64) - survived until the first hosted ingest.

This only covers what the recorded fixtures happen to contain. It is a guard
against the obvious case, not a proof: see the outstanding list in
docs/decisions/README.md.
"""

from __future__ import annotations

import pytest
from sqlalchemy import String
from tests.conftest import load_ted_fixture

from watchdog.sources.ted import map_notice
from watchdog.storage.tables import TenderRow

_LIMITS = {
    column.name: column.type.length
    for column in TenderRow.__table__.columns
    if isinstance(column.type, String) and column.type.length
}


def test_the_mapped_notice_fits_every_declared_column_length(ted_fixture_name: str) -> None:
    tender = map_notice(load_ted_fixture(ted_fixture_name))
    stored = tender.model_dump(mode="json")

    too_long = {
        name: (len(value), limit)
        for name, limit in _LIMITS.items()
        if isinstance(value := stored.get(name), str) and len(value) > limit
    }

    assert not too_long, f"{ted_fixture_name}: {too_long} (length, declared limit)"


@pytest.mark.parametrize(
    "name",
    [
        "title",
        "title_native",
        "description",
        "buyer_name",
        "place_of_performance",
        "deadline_source",
        "source_url",
        "documents_url",
    ],
)
def test_free_text_columns_carry_no_length_bound(name: str) -> None:
    """A bound belongs on a code, never on anything a buyer or a source composes."""
    assert name not in _LIMITS, f"{name} is bounded at {_LIMITS.get(name)} characters"
