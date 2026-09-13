"""The source's codes, and what they are allowed to be turned into.

Every number-kind case here was read off a real notice rather than a
specification, because the failure mode is silent: a rank of 1 shown as "1%"
looks entirely reasonable and is a different claim from the one the buyer made.
"""

from __future__ import annotations

import pytest

from watchdog.core.codelists import (
    criterion_number,
    has_qualification_stage,
    is_utility_activity,
    language_name,
    language_names,
    main_activity_label,
    number_kind_label,
    procedure_type_label,
)

# ------------------------------------------------------------ criterion numbers


@pytest.mark.parametrize(
    ("value", "kind", "expected"),
    [
        # 596416-2026 states "waga 100%" beside number 100 with per-exa, and the
        # numbers of 313 single-code notices total exactly 100.
        ("40", "per-exa", "40%"),
        # 541548-2026 names its criterion "Hinnan maksimipistemaara" - maximum
        # points for price. Totals of 1000 and 0 also occur, which no percentage can.
        ("55", "poi-exa", "55 points"),
        # 534739-2026 scores Prijs 1 and Dienstverlening 2. A rank, not a quantity.
        ("1", "ord-imp", "ranked 1"),
    ],
)
def test_a_number_is_written_with_the_meaning_that_was_verified(
    value: str, kind: str, expected: str
) -> None:
    assert criterion_number(value, kind) == expected


def test_a_decimal_weighting_is_never_turned_into_a_percentage() -> None:
    # 565418-2026 uses 0.8 and 0.2. As "0.8%" it would be out by a factor of 100.
    assert criterion_number("0.8", "dec-exa") == "0.8"


def test_a_number_with_no_stated_meaning_is_returned_exactly_as_it_arrived() -> None:
    assert criterion_number("40", None) == "40"
    assert criterion_number("40", "some-code-ted-added-later") == "40"


def test_trailing_zeros_the_source_sent_are_not_tidied_away() -> None:
    # 533954-2026 sends "55.0000". It is source text, not a number we computed.
    assert criterion_number("55.0000", "poi-exa") == "55.0000 points"


def test_an_unknown_number_kind_has_no_label_so_the_caller_can_say_so() -> None:
    assert number_kind_label("per-exa") == "percentage"
    assert number_kind_label("something-new") is None
    assert number_kind_label(None) is None


def test_no_number_is_nothing_rather_than_an_empty_string_dressed_up() -> None:
    assert criterion_number(None, "per-exa") is None
    assert criterion_number("   ", "per-exa") is None


# ------------------------------------------------------------------ procedures


def test_the_qualification_stage_is_not_just_the_restricted_procedure() -> None:
    """Measured over 2,158 notices: open 1,637, neg-w-call 321, restricted 68.

    Reading this as "open versus restricted" would describe 3% of the population
    and be wrong about the 15% that negotiate after a call.
    """
    assert has_qualification_stage("open") is False
    assert has_qualification_stage("restricted") is True
    assert has_qualification_stage("neg-w-call") is True
    assert has_qualification_stage("comp-dial") is True


def test_an_unstated_or_unknown_procedure_answers_neither_way() -> None:
    assert has_qualification_stage(None) is None
    assert has_qualification_stage("a-code-we-do-not-know") is None


def test_a_procedure_we_cannot_name_returns_nothing_rather_than_a_guess() -> None:
    assert procedure_type_label("open") is not None
    assert procedure_type_label("a-code-we-do-not-know") is None


# --------------------------------------------------------------- main activity


def test_a_utility_is_told_apart_from_an_authority() -> None:
    assert is_utility_activity("electricity") is True
    assert is_utility_activity("gas-heat") is True
    assert is_utility_activity("gen-pub") is False
    assert is_utility_activity("health") is False


def test_an_unknown_activity_answers_neither_way() -> None:
    assert is_utility_activity(None) is None
    assert is_utility_activity("a-code-we-do-not-know") is None
    assert main_activity_label("a-code-we-do-not-know") is None


# ------------------------------------------------------------------ languages


def test_languages_are_named_from_the_code_the_source_sends() -> None:
    assert language_name("CAT") == "Catalan"
    assert language_name("spa") == "Spanish", "TED is not consistent about case"


def test_a_language_we_cannot_name_falls_back_to_its_code() -> None:
    assert language_name("ZZZ") is None
    assert language_names(["SWE", "ZZZ"]) == ["Swedish", "ZZZ"]
