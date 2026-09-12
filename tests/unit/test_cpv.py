"""CPV matching: hierarchy, leading zeros, and the division floor.

The hierarchy cases are the ones TED itself demonstrated on notice 597239-2026,
which carries 71318100 and no other 713 code.
"""

from __future__ import annotations

import pytest

from watchdog.core.cpv import (
    cpv_matches,
    matched_cpv_codes,
    normalise_cpv,
    normalise_cpv_list,
    significant_prefix,
)


@pytest.mark.parametrize(
    "configured",
    ["71318100", "71318000", "71310000", "71300000", "71000000"],
)
def test_a_child_code_matches_every_ancestor(configured: str) -> None:
    # TED returns 597239-2026 for all of these; our matcher must agree.
    assert cpv_matches(configured, "71318100")


def test_a_child_code_does_not_match_an_unrelated_division() -> None:
    assert not cpv_matches("45000000", "71318100")


def test_startswith_on_the_padded_code_would_have_failed() -> None:
    # The reason significant_prefix exists: the naive version is False here.
    assert not "71318100".startswith("71318000")
    assert cpv_matches("71318000", "71318100")


def test_a_division_code_matches_only_its_own_division() -> None:
    assert cpv_matches("70000000", "70000000")
    assert cpv_matches("70000000", "70123456")
    assert not cpv_matches("70000000", "71000000")
    assert not cpv_matches("70000000", "79000000")


def test_the_prefix_never_shrinks_below_a_division() -> None:
    assert significant_prefix("70000000") == "70"
    assert significant_prefix("71318000") == "71318"
    assert significant_prefix("09300000") == "093"


def test_a_leading_zero_survives_normalisation() -> None:
    assert normalise_cpv("09310000") == "09310000"
    # PyYAML hands back an int for an unquoted code; the zero has to come back.
    assert normalise_cpv(9310000) == "09310000"
    assert normalise_cpv(71241000) == "71241000"


def test_a_leading_zero_code_still_matches_its_children() -> None:
    assert cpv_matches("09300000", "09310000")
    assert cpv_matches("09330000", "09331200")
    assert not cpv_matches("09330000", "71241000")


def test_a_check_digit_suffix_is_dropped() -> None:
    assert normalise_cpv("71241000-9") == "71241000"


@pytest.mark.parametrize("value", [None, "", "   ", "abc", "71241000000", "712-4", "712"])
def test_anything_that_is_not_a_cpv_code_is_none(value: str | None) -> None:
    assert normalise_cpv(value) is None


def test_a_truncated_code_is_refused_rather_than_padded() -> None:
    # "712" padded to "00000712" would be a different code that matches nothing.
    # A real code loses at most one leading zero, because the lowest division is 03.
    assert normalise_cpv("712") is None
    assert normalise_cpv("9310000") == "09310000"


def test_a_non_code_never_matches() -> None:
    assert not cpv_matches("not-a-code", "71241000")
    assert not cpv_matches("71241000", "not-a-code")


def test_matched_codes_report_which_code_matched() -> None:
    matched = matched_cpv_codes(
        ["71318000", "09330000"],
        ["45000000", "71318100", "09331200", "33696500"],
    )

    # A score has to be able to say which code it matched on.
    assert matched == ["71318100", "09331200"]


def test_normalising_a_list_deduplicates_and_keeps_order() -> None:
    codes = normalise_cpv_list(["71241000", "71241000", 9310000, "71241000-9"])

    assert codes == ["71241000", "09310000"]


def test_an_empty_configuration_matches_nothing() -> None:
    assert matched_cpv_codes([], ["71241000"]) == []
