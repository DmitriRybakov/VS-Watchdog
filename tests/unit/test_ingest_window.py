"""Where a run starts, and why.

The window is the whole safety mechanism: get it wrong by a few hours and a
notice published between two runs is never fetched at all. Every case here is in
UTC, and two of them straddle a date boundary on purpose, because a window that
looks right in local time is a day short west of Greenwich.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from watchdog.core.enums import SourcePlatform
from watchdog.core.models import Watermark
from watchdog.services.ingest import DEFAULT_OVERLAP_HOURS, plan_window

NOW = datetime(2026, 3, 1, 6, 0, tzinfo=UTC)


def watermark_at(moment: datetime) -> Watermark:
    return Watermark(source=SourcePlatform.TED, last_successful_at=moment, updated_at=moment)


def test_the_first_run_reaches_back_the_configured_backfill() -> None:
    window = plan_window(None, now=NOW, backfill_days=30)

    assert window.window_from == datetime(2026, 1, 30, 6, 0, tzinfo=UTC)
    assert window.window_to == NOW
    assert "first run" in window.reason


def test_a_source_with_a_watermark_row_but_no_timestamp_is_still_a_first_run() -> None:
    empty = Watermark(source=SourcePlatform.TED, last_successful_at=None, updated_at=NOW)

    window = plan_window(empty, now=NOW, backfill_days=7)

    assert window.window_from == datetime(2026, 2, 22, 6, 0, tzinfo=UTC)


def test_the_overlap_reaches_back_forty_eight_hours_across_a_date_boundary() -> None:
    # Half past midnight UTC: anything that rounds to "yesterday" loses a day.
    window = plan_window(watermark_at(datetime(2026, 3, 1, 0, 30, tzinfo=UTC)), now=NOW)

    assert DEFAULT_OVERLAP_HOURS == 48
    assert window.window_from == datetime(2026, 2, 27, 0, 30, tzinfo=UTC)
    assert window.query_dates == (date(2026, 2, 27), date(2026, 3, 1))


def test_the_overlap_crosses_a_month_and_a_leap_day_without_help() -> None:
    window = plan_window(
        watermark_at(datetime(2028, 3, 1, 1, 0, tzinfo=UTC)),
        now=datetime(2028, 3, 1, 6, 0, tzinfo=UTC),
    )

    assert window.window_from == datetime(2028, 2, 28, 1, 0, tzinfo=UTC)


def test_a_watermark_given_in_another_timezone_is_compared_in_utc() -> None:
    # 02:00 in Oslo on 1 March is 01:00 UTC, so the window starts on 27 February.
    oslo = datetime.fromisoformat("2026-03-01T02:00:00+01:00")

    window = plan_window(watermark_at(oslo), now=NOW)

    assert window.window_from == datetime(2026, 2, 27, 1, 0, tzinfo=UTC)
    assert window.query_dates[0] == date(2026, 2, 27)


def test_since_days_overrides_the_watermark() -> None:
    watermark = watermark_at(datetime(2026, 2, 28, 6, 0, tzinfo=UTC))

    window = plan_window(watermark, now=NOW, since_days=7)

    assert window.window_from == datetime(2026, 2, 22, 6, 0, tzinfo=UTC)
    assert "7 day(s) back" in window.reason


def test_a_full_backfill_starts_at_midnight_utc_on_the_date_given() -> None:
    window = plan_window(
        watermark_at(datetime(2026, 2, 28, 6, 0, tzinfo=UTC)),
        now=NOW,
        backfill_from=date(2026, 1, 1),
    )

    assert window.window_from == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    assert "full backfill" in window.reason


def test_a_window_that_would_start_in_the_future_is_refused() -> None:
    with pytest.raises(ValueError, match="in the future"):
        plan_window(None, now=NOW, backfill_from=date(2026, 6, 1))
