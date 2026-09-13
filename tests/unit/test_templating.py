"""The display filters: the small rules that stop a value claiming more than it is.

These are one-line functions, and every one of them exists because the obvious
version is wrong in a way nobody would spot on screen.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from watchdog.core.models import AwardCriterion, ContractDuration
from watchdog.web.templating import (
    ABSENT,
    NOT_IN_NOTICE,
    criterion_weight,
    duration,
    given,
    languages,
    moment,
)

WARSAW = timezone(timedelta(hours=2))
NEW_YORK = timezone(timedelta(hours=-5))


# ------------------------------------------------------------------ utc times


def test_a_deadline_after_midnight_local_shows_the_previous_day_in_utc() -> None:
    """00:30 in Warsaw on the 28th is 22:30 on the 27th in UTC.

    The failure this guards against is appending "UTC" to a local time: the clock
    would be two hours out and the *date* would be a day out, which reads as a
    deadline that has not passed when it has.
    """
    local = datetime(2026, 9, 28, 0, 30, tzinfo=WARSAW)

    assert moment(local) == "2026-09-27 22:30 UTC"
    assert moment(local) != "2026-09-28 00:30 UTC"


def test_a_deadline_late_in_the_evening_west_of_greenwich_shows_the_next_day() -> None:
    local = datetime(2026, 9, 28, 23, 59, tzinfo=NEW_YORK)

    assert moment(local) == "2026-09-29 04:59 UTC"


def test_a_timestamp_already_in_utc_is_unchanged() -> None:
    assert moment(datetime(2026, 9, 28, 8, 15, tzinfo=UTC)) == "2026-09-28 08:15 UTC"


def test_a_naive_timestamp_is_refused_rather_than_labelled() -> None:
    with pytest.raises(ValueError, match="naive"):
        moment(datetime(2026, 9, 28, 8, 15))


def test_no_timestamp_is_a_dash_and_never_today() -> None:
    assert moment(None) == "\u2013"


# --------------------------------------------------------------- the absences


def test_an_absent_source_value_does_not_claim_we_read_the_notice() -> None:
    # TED returned no value in that field. The fact may still be in the notice
    # text or in a document nobody has fetched, and "Not in the notice" would
    # stop somebody going to look.
    assert given(None) == "Not provided in the retrieved data"
    assert "notice" not in NOT_IN_NOTICE.lower()


def test_the_three_absences_stay_three_different_sentences() -> None:
    assert len(set(ABSENT.values())) == 3


# ---------------------------------------------------------- criterion numbers


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        # Verified against real notices; see core.codelists.
        ("per-exa", "40%"),
        ("poi-exa", "40 points"),
        ("ord-imp", "ranked 40"),
    ],
)
def test_a_criterion_number_is_written_with_its_verified_meaning(kind: str, expected: str) -> None:
    assert criterion_weight(AwardCriterion(number="40", number_kind=kind)) == expected


def test_a_number_whose_meaning_is_unknown_is_shown_bare_and_said_to_be() -> None:
    written = criterion_weight(AwardCriterion(number="40", number_kind=None))

    assert written.startswith("40")
    assert "%" not in written
    assert "did not say" in written


def test_a_decimal_weighting_is_never_written_as_a_percentage() -> None:
    # 565418-2026 uses 0.8 and 0.2. As a percentage it would be out by a hundred.
    written = criterion_weight(AwardCriterion(number="0.8", number_kind="dec-exa"))

    assert "%" not in written
    assert "0.8" in written


def test_a_criterion_with_no_number_says_nothing() -> None:
    assert criterion_weight(AwardCriterion(type="quality")) == ""


# ----------------------------------------------------------------- the rest


def test_a_duration_always_carries_its_unit() -> None:
    assert duration(ContractDuration(value="10", unit="MONTH")) == "10 months"
    assert duration(ContractDuration(value="1", unit="YEAR")) == "1 year"


def test_a_duration_with_no_unit_says_so_rather_than_guessing_one() -> None:
    assert duration(ContractDuration(value="10")) == "10 (unit not stated)"


def test_languages_are_named_and_an_unknown_code_is_shown_as_itself() -> None:
    assert languages(["CAT", "SPA"]) == "Catalan, Spanish"
    assert languages(["ZZZ"]) == "ZZZ"
