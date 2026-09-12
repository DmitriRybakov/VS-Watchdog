"""Time handling. UTC is the only timezone in the system.

Every stored, compared and logged timestamp is timezone-aware UTC. Conversion to
a local timezone happens in the display layer and nowhere else.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """The current moment, timezone-aware in UTC."""
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` as UTC. A naive timestamp is an error, not a guess."""
    if value.tzinfo is None:
        raise ValueError(
            "naive datetime: the offset must be known before it is stored "
            "(attach the source's timezone, then convert)"
        )
    return value.astimezone(UTC)
