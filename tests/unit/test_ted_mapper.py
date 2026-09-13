"""The TED mapper, field by field, against recorded responses.

Every fixture is named for the thing it proves. None of these tests touch the
network; see tests/fixtures/ted/README.md for where each file came from.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from tests.conftest import load_ted_fixture

from watchdog.core.enums import ContractNature, DeadlineType, NoticeStage, SourcePlatform
from watchdog.core.models import ContractDuration
from watchdog.sources.errors import MappingError
from watchdog.sources.ted.mapper import REJECTED_DEADLINE_FIELDS, map_notice


@pytest.fixture
def competition() -> dict:
    return load_ted_fixture("form_type_competition_is_a_contract_notice")


# ----------------------------------------------------------------- identity


def test_the_publication_number_is_the_identity(competition: dict) -> None:
    tender = map_notice(competition)

    assert tender.source is SourcePlatform.TED
    assert tender.source_id == competition["publication-number"]
    assert tender.id == f"ted:{competition['publication-number']}"


def test_a_notice_without_a_publication_number_cannot_be_mapped() -> None:
    with pytest.raises(MappingError):
        map_notice({"notice-title": {"eng": "x"}})


def test_a_mapping_failure_carries_the_publication_number() -> None:
    broken = {"publication-number": "123456-2026"}

    with pytest.raises(MappingError) as caught:
        map_notice(broken)

    assert caught.value.source_id == "123456-2026"
    assert "123456-2026" in str(caught.value)


# -------------------------------------------------------------------- title


def test_the_display_title_is_the_composed_english_one() -> None:
    notice = load_ted_fixture("notice_title_in_24_languages")
    tender = map_notice(notice)

    assert tender.title == notice["notice-title"]["eng"]
    assert tender.title_language == "eng"
    # TED composes it as "<country> - <CPV label> - <buyer's title>".
    assert tender.title.startswith("France")


def test_ted_supplies_the_composed_title_in_every_eu_language() -> None:
    notice = load_ted_fixture("notice_title_in_24_languages")

    assert len(notice["notice-title"]) == 24
    # Which is why title_language "eng" records nothing useful on its own, and
    # why the buyer's own language is kept separately.
    assert map_notice(notice).title_native_language != "eng"


def test_the_native_title_is_the_buyers_own_words() -> None:
    notice = load_ted_fixture("notice_title_in_24_languages")
    tender = map_notice(notice)

    assert tender.title_native == notice["title-proc"]["fra"]
    assert tender.title_native_language == "fra"
    # The buyer's title is not translated; it is the third segment of the display one.
    assert tender.title_native in tender.title


def test_a_bilingual_buyer_keeps_both_titles() -> None:
    notice = load_ted_fixture("bilingual_title_proc")
    tender = map_notice(notice)

    languages = set(notice["title-proc"])
    assert len(languages) == 2

    # One is chosen for display, but both are screened.
    assert tender.title_native_language in languages
    screened = {block.language for block in tender.screening_blocks if block.field == "title-proc"}
    assert screened == languages


def test_the_chosen_language_is_repeatable_and_not_dictionary_order() -> None:
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "display"},
        "title-proc": {"swe": "svensk", "dan": "dansk"},
    }
    reordered = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "display"},
        "title-proc": {"dan": "dansk", "swe": "svensk"},
    }

    # No English, no official-language: alphabetically first, so it is stable.
    assert map_notice(notice).title_native_language == "dan"
    assert map_notice(reordered).title_native_language == "dan"


def test_english_is_preferred_only_when_the_buyer_published_it() -> None:
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "England - Consultancy - whatever"},
        "title-proc": {"eng": "What the buyer wrote", "cym": "Beth ysgrifennodd"},
        "official-language": ["CYM"],
    }

    assert map_notice(notice).title_native_language == "eng"


def test_a_notice_with_no_title_proc_is_kept_not_discarded() -> None:
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "Norway - Engineering services - something"},
        "description-proc": {"nor": "En beskrivelse av oppdraget."},
    }
    tender = map_notice(notice)

    assert tender.title_native is None
    assert tender.title_native_language is None
    # Still screened, on its description.
    assert [block.field for block in tender.screening_blocks] == ["description-proc"]


def test_a_notice_with_neither_title_cannot_be_mapped() -> None:
    with pytest.raises(MappingError, match="neither notice-title nor title-proc"):
        map_notice({"publication-number": "1-2026", "description-proc": {"eng": "text"}})


# ----------------------------------------------------------- screening text


def test_screening_text_never_contains_the_composed_title() -> None:
    notice = load_ted_fixture("notice_title_in_24_languages")
    tender = map_notice(notice)

    # The composed title restates the CPV label we filtered on. Screening it
    # would make an activity rule fire on every notice.
    assert "Feasibility study, advisory service, analysis" not in tender.screening_text
    assert tender.title_native is not None
    assert tender.title_native in tender.screening_text


def test_every_block_is_labelled_with_its_field_and_language() -> None:
    tender = map_notice(load_ted_fixture("bilingual_title_proc"))

    assert "[title-proc/fra]" in tender.screening_text
    assert "[title-proc/nld]" in tender.screening_text


def test_identical_lot_descriptions_are_collapsed_to_one_block() -> None:
    notice = load_ted_fixture("notice_title_in_24_languages")
    lot_descriptions = notice["description-lot"]["fra"]
    tender = map_notice(notice)

    assert len(lot_descriptions) == 6
    assert len(set(lot_descriptions)) == 1

    lot_blocks = [block for block in tender.screening_blocks if block.field == "description-lot"]
    assert len(lot_blocks) == 1


def test_distinct_lot_descriptions_each_become_a_block() -> None:
    notice = load_ted_fixture("six_lots_with_unaligned_parallel_arrays")
    tender = map_notice(notice)

    lot_blocks = [block for block in tender.screening_blocks if block.field == "description-lot"]
    distinct = {text.strip() for text in next(iter(notice["description-lot"].values()))}

    assert len(lot_blocks) == len(distinct)


def test_blocks_are_ordered_the_same_way_every_time() -> None:
    notice = load_ted_fixture("bilingual_title_proc")

    first = [(b.field, b.language) for b in map_notice(notice).screening_blocks]
    second = [(b.field, b.language) for b in map_notice(notice).screening_blocks]

    assert first == second
    assert first[0][0] == "title-proc"


def test_the_content_hash_covers_every_block_not_just_one_language() -> None:
    notice = load_ted_fixture("bilingual_title_proc")
    tender = map_notice(notice)

    corrected = dict(notice)
    corrected["title-proc"] = dict(notice["title-proc"])
    language = sorted(corrected["title-proc"])[-1]
    corrected["title-proc"][language] = "een gecorrigeerde titel"

    # A corrected variant in any language must make the screening stale.
    assert map_notice(corrected).content_hash != tender.content_hash


def test_the_content_hash_covers_every_cpv_code() -> None:
    notice = load_ted_fixture("form_type_competition_is_a_contract_notice")
    tender = map_notice(notice)

    changed = dict(notice)
    changed["classification-cpv"] = [*notice.get("classification-cpv", []), "09330000"]

    assert map_notice(changed).content_hash != tender.content_hash


# ----------------------------------------------------------------- deadlines


def test_the_tender_deadline_combines_the_date_and_the_time(competition: dict) -> None:
    tender = map_notice(competition)

    assert competition["deadline-receipt-tender-date-lot"]
    assert tender.deadline is not None
    assert tender.deadline.tzinfo is UTC
    assert tender.deadline_type is DeadlineType.TENDER_SUBMISSION
    assert tender.deadline_source == (
        "deadline-receipt-tender-date-lot+deadline-receipt-tender-time-lot"
    )
    # The date the buyer stated, not its UTC equivalent.
    assert tender.deadline_date == date.fromisoformat(
        competition["deadline-receipt-tender-date-lot"][0][:10]
    )


def test_the_offset_is_honoured_and_not_assumed() -> None:
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "deadline-receipt-tender-date-lot": ["2026-09-28+02:00"],
        "deadline-receipt-tender-time-lot": ["10:15:00+02:00"],
    }
    tender = map_notice(notice)

    assert tender.deadline == datetime(2026, 9, 28, 8, 15, tzinfo=UTC)
    assert tender.deadline_date == date(2026, 9, 28)


def test_a_deadline_just_after_midnight_lands_on_the_previous_day_in_utc() -> None:
    """00:30 in Warsaw on the 28th is 22:30 on the 27th in UTC.

    Both facts are kept and they are different: ``deadline`` is the real moment,
    converted, and ``deadline_date`` is the calendar date the buyer wrote. A
    cross-check on TED's own page has to show the buyer's date, and a filter on
    "has it passed" has to use the moment.
    """
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "deadline-receipt-tender-date-lot": ["2026-09-28+02:00"],
        "deadline-receipt-tender-time-lot": ["00:30:00+02:00"],
    }
    tender = map_notice(notice)

    assert tender.deadline == datetime(2026, 9, 27, 22, 30, tzinfo=UTC)
    assert tender.deadline.date() == date(2026, 9, 27), "the UTC day is the day before"
    assert tender.deadline_date == date(2026, 9, 28), "the buyer's stated day is unchanged"


def test_a_deadline_late_in_the_evening_west_of_greenwich_lands_on_the_next_day() -> None:
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "deadline-receipt-tender-date-lot": ["2026-09-28-05:00"],
        "deadline-receipt-tender-time-lot": ["23:59:00-05:00"],
    }
    tender = map_notice(notice)

    assert tender.deadline == datetime(2026, 9, 29, 4, 59, tzinfo=UTC)
    assert tender.deadline_date == date(2026, 9, 28)


def test_a_date_with_no_time_leaves_the_moment_null() -> None:
    # Never observed in 1250 sampled notices, but an invented 23:59 would look
    # real to a filter and to an export.
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "deadline-receipt-tender-date-lot": ["2026-09-28+02:00"],
    }
    tender = map_notice(notice)

    assert tender.deadline is None
    assert tender.deadline_date == date(2026, 9, 28)
    assert tender.deadline_source == "deadline-receipt-tender-date-lot"
    assert tender.deadline_type is DeadlineType.TENDER_SUBMISSION


def test_lots_with_different_deadlines_take_the_earliest_and_set_multi_lot() -> None:
    notice = load_ted_fixture("lots_with_different_deadlines")
    tender = map_notice(notice)

    dates = notice["deadline-receipt-tender-date-lot"]
    assert len(set(dates)) > 1

    assert tender.deadline_date == min(date.fromisoformat(value[:10]) for value in dates)
    assert tender.multi_lot is True
    # The full set stays inspectable rather than being reduced to the one we used.
    assert tender.raw["deadline-receipt-tender-date-lot"] == dates


def test_a_notice_with_no_deadline_has_none_of_them() -> None:
    tender = map_notice(load_ted_fixture("no_deadline_of_any_kind"))

    assert tender.deadline is None
    assert tender.deadline_date is None
    assert tender.deadline_source is None
    assert tender.deadline_type is DeadlineType.UNKNOWN


def test_the_union_field_is_a_fallback_and_its_meaning_is_unknown() -> None:
    # A qualification system's only deadline is a participation deadline, and
    # deadline-receipt-request does not say which of the three it is.
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "deadline-receipt-request": ["2026-09-24T12:00:00+02:00"],
    }
    tender = map_notice(notice)

    assert tender.deadline == datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
    assert tender.deadline_source == "deadline-receipt-request"
    assert tender.deadline_type is DeadlineType.UNKNOWN


def test_a_participation_deadline_is_labelled_as_one() -> None:
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "deadline-receipt-request-date-lot": ["2026-09-24+02:00"],
        "deadline-receipt-request-time-lot": ["12:00:00+02:00"],
        "deadline-receipt-request": ["2026-09-24T12:00:00+02:00"],
    }

    assert map_notice(notice).deadline_type is DeadlineType.PARTICIPATION_REQUEST


def test_an_expression_of_interest_deadline_is_labelled_as_one() -> None:
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "deadline-receipt-expressions-date-lot": ["2026-09-24+02:00"],
        "deadline-receipt-expressions-time-lot": ["12:00:00+02:00"],
    }

    assert map_notice(notice).deadline_type is DeadlineType.EXPRESSION_OF_INTEREST


def test_the_rejected_deadline_fields_are_never_read() -> None:
    # On 597197-2026 `deadline` says 2026-08-18 while the tender deadline is
    # 2026-09-07. Three weeks early is worse than nothing.
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "deadline": ["2026-08-18T22:00:00Z"],
        "deadline-date-lot": ["2026-08-18Z"],
        "deadline-time-lot": ["22:00:00Z"],
        "deadline-receipt-tender-date-lot": ["2026-09-07Z"],
        "deadline-receipt-tender-time-lot": ["21:59:59Z"],
    }
    tender = map_notice(notice)

    assert tender.deadline_date == date(2026, 9, 7)
    read_fields = set((tender.deadline_source or "").split("+"))
    assert read_fields.isdisjoint(REJECTED_DEADLINE_FIELDS)


def test_the_rejected_fields_alone_produce_no_deadline_at_all() -> None:
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "deadline": ["2026-08-18T22:00:00Z"],
        "deadline-date-lot": ["2026-08-18Z"],
    }
    tender = map_notice(notice)

    assert tender.deadline is None
    assert tender.deadline_date is None


# ----------------------------------------------------------------------- cpv


def test_cpv_main_and_additional_come_from_the_procedure_level_fields(competition: dict) -> None:
    tender = map_notice(competition)

    assert tender.cpv_main == competition["main-classification-proc"][0]
    assert tender.cpv_additional == competition.get("additional-classification-proc", [])


def test_cpv_all_is_the_deduplicated_union_including_lot_codes() -> None:
    notice = load_ted_fixture("cpv_child_code_matches_parent_filter")
    tender = map_notice(notice)

    assert len(notice["classification-cpv"]) > len(set(notice["classification-cpv"]))
    assert tender.cpv_all == list(dict.fromkeys(notice["classification-cpv"]))
    # The code that made this notice interesting is not the procedure-level one.
    assert "71318100" in tender.cpv_all
    assert tender.cpv_main != "71318100"


def test_a_classification_from_another_scheme_is_not_stored_as_cpv() -> None:
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "main-classification-proc": ["12345678"],
        "main-classification-type-proc": "something-else",
    }

    assert map_notice(notice).cpv_main is None


def test_a_leading_zero_code_round_trips_through_the_mapper() -> None:
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "classification-cpv": ["09310000"],
        "main-classification-proc": ["09310000"],
        "main-classification-type-proc": "cpv",
    }
    tender = map_notice(notice)

    assert tender.cpv_main == "09310000"
    assert tender.cpv_all == ["09310000"]


# ------------------------------------------------------------ contract nature


def test_a_works_notice_with_a_services_component_lists_both() -> None:
    notice = load_ted_fixture("mixed_nature_works_with_services")
    tender = map_notice(notice)

    assert ContractNature.SERVICES in tender.contract_natures
    assert ContractNature.WORKS in tender.contract_natures
    # The scalar keeps TED's own procedure-level answer, which is not services.
    assert tender.contract_nature is ContractNature.WORKS
    assert tender.is_services is True


def test_a_plain_services_notice_lists_only_services(competition: dict) -> None:
    if "services" not in (competition.get("contract-nature") or []):
        pytest.skip("the recorded competition notice is not a services notice")

    tender = map_notice(competition)

    assert tender.contract_natures == [ContractNature.SERVICES]
    assert tender.contract_nature is ContractNature.SERVICES


def test_an_unknown_nature_is_not_invented() -> None:
    notice = {"publication-number": "1-2026", "notice-title": {"eng": "x"}}

    tender = map_notice(notice)
    assert tender.contract_nature is ContractNature.UNKNOWN
    assert tender.contract_natures == []


# ---------------------------------------------------------------------- stage


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("form_type_competition_is_a_contract_notice", NoticeStage.CONTRACT_NOTICE),
        ("form_type_planning_is_prior_information", NoticeStage.PRIOR_INFORMATION),
        ("form_type_consultation_is_a_market_consultation", NoticeStage.MARKET_CONSULTATION),
    ],
)
def test_the_stage_comes_from_form_type(fixture: str, expected: NoticeStage) -> None:
    tender = map_notice(load_ted_fixture(fixture))

    assert tender.notice_stage is expected


def test_prior_information_and_market_consultation_stay_separate() -> None:
    planning = map_notice(load_ted_fixture("form_type_planning_is_prior_information"))
    consultation = map_notice(load_ted_fixture("form_type_consultation_is_a_market_consultation"))

    assert planning.notice_stage is not consultation.notice_stage


def test_notice_type_is_the_second_opinion_when_form_type_says_nothing() -> None:
    notice = {"publication-number": "1-2026", "notice-title": {"eng": "x"}, "notice-type": "pmc"}

    assert map_notice(notice).notice_stage is NoticeStage.MARKET_CONSULTATION


def test_the_subtype_is_stored_verbatim_and_not_interpreted(competition: dict) -> None:
    tender = map_notice(competition)

    assert tender.notice_subtype == competition.get("notice-subtype")


# ---------------------------------------------------------------- value, lots


def test_the_notice_level_value_is_a_decimal_with_its_currency() -> None:
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "estimated-value-proc": "1495499.49",
        "estimated-value-cur-proc": "EUR",
    }
    tender = map_notice(notice)

    assert tender.estimated_value == Decimal("1495499.49")
    assert tender.currency == "EUR"


def test_a_value_without_a_currency_is_not_stored() -> None:
    # A bare number reads as euros to whoever sees it next.
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "estimated-value-proc": "48000000",
    }
    tender = map_notice(notice)

    assert tender.estimated_value is None
    assert tender.currency is None


def test_unaligned_lot_arrays_are_never_zipped_together() -> None:
    notice = load_ted_fixture("six_lots_with_unaligned_parallel_arrays")
    tender = map_notice(notice)

    assert len(notice["estimated-value-lot"]) == 6
    assert len(notice["estimated-value-cur-lot"]) == 1

    # Only the notice-level pair is mapped; the lot arrays stay in raw.
    assert tender.multi_lot is True
    assert tender.raw["estimated-value-lot"] == notice["estimated-value-lot"]
    if tender.estimated_value is not None:
        assert str(tender.estimated_value) == notice["estimated-value-proc"]


def test_repeated_identical_lot_entries_still_mean_several_lots() -> None:
    notice = load_ted_fixture("notice_title_in_24_languages")
    tender = map_notice(notice)

    assert len(notice["description-lot"]["fra"]) == 6
    assert tender.multi_lot is True


def test_a_single_lot_notice_is_not_marked_multi_lot() -> None:
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "description-lot": {"eng": ["one lot"]},
        "estimated-value-lot": ["100"],
    }

    assert map_notice(notice).multi_lot is False


# ------------------------------------------------------------ lot association


def test_the_lot_count_comes_from_teds_own_identifiers() -> None:
    notice = load_ted_fixture("six_lots_with_unaligned_parallel_arrays")
    tender = map_notice(notice)

    assert notice["identifier-lot"] == [f"LOT-000{n}" for n in range(1, 7)]
    assert tender.lot_ids == notice["identifier-lot"]
    assert tender.lot_count == 6
    assert tender.multi_lot is True


def test_lot_identifiers_are_not_necessarily_contiguous() -> None:
    """458521-2026 carries LOT-0001 and LOT-0003. Position is not a lot number."""
    notice = load_ted_fixture("two_lots_seven_award_criteria")
    tender = map_notice(notice)

    assert tender.lot_ids == ["LOT-0001", "LOT-0003"]
    assert tender.lot_count == 2


def test_arrays_disagreeing_in_length_do_not_make_a_notice_multi_lot() -> None:
    # Two different facts. A mismatch says we cannot associate the values; it does
    # not say how many lots there are, and TED's identifiers already did.
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "identifier-lot": ["LOT-0001"],
        "submission-language": ["ENG", "FRA", "DEU"],
        "document-url-lot": ["https://a.example", "https://b.example"],
    }
    tender = map_notice(notice)

    assert tender.lot_count == 1
    assert tender.multi_lot is False
    assert tender.submission_languages == ["ENG", "FRA", "DEU"]


def test_the_lot_arrays_stand_in_only_when_there_are_no_identifiers() -> None:
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "description-lot": {"eng": ["one", "two", "three"]},
    }
    tender = map_notice(notice)

    assert tender.lot_ids == []
    assert tender.lot_count is None
    assert tender.multi_lot is True


def test_nothing_lot_scoped_is_attributed_to_a_lot() -> None:
    """The whole of decision 0008, on the notice that motivated it.

    532622-2026 has five lots, four estimated values, one currency and eight
    submission languages. Every count differs, and no field says which lot any of
    them belongs to, so the mapper holds them as sets across lots and the Tender
    has nowhere to put a per-lot value even if someone wanted to.
    """
    notice = load_ted_fixture("lot_values_and_languages_do_not_match_the_lots")
    tender = map_notice(notice)

    assert tender.lot_count == 5
    assert len(notice["estimated-value-lot"]) == 4
    assert len(notice["submission-language"]) == 8

    assert len(tender.lot_values) == 4, "kept as they arrived, not padded to the lot count"
    assert tender.submission_languages == ["CAT", "SPA"], "the distinct set, across lots"
    assert not hasattr(tender, "lots"), "there is no per-lot structure to fill in wrongly"


def test_lot_values_are_never_summed_into_a_procedure_total() -> None:
    notice = load_ted_fixture("lot_values_and_languages_do_not_match_the_lots")
    tender = map_notice(notice)

    total = sum(tender.lot_values)
    assert tender.estimated_value is not None
    assert tender.estimated_value != total, "four lot values are not the five-lot procedure"
    assert str(tender.estimated_value) == notice["estimated-value-proc"]


# --------------------------------------------------------------- the criteria


def test_award_criteria_are_read_by_position_when_the_arrays_agree() -> None:
    """458521-2026: seven criteria over two lots, four then three.

    The pairing claim is only that one criterion's own parts line up, and the
    values show it: "Honorar" is the cost criterion in both groups and carries 40
    in the first and 50 in the second. No criterion is attached to a lot.
    """
    tender = map_notice(load_ted_fixture("two_lots_seven_award_criteria"))

    assert tender.criteria_unpaired is False
    assert len(tender.award_criteria) == 7
    assert tender.lot_count == 2, "seven criteria, two lots, and no way to group them"

    first = tender.award_criteria[0]
    assert first.type == "cost"
    assert first.name == "Honorar"
    assert first.number == "40"
    assert first.number_kind == "per-exa"

    fifth = tender.award_criteria[4]
    assert fifth.name == "Honorar"
    assert fifth.number == "50"

    assert [c.type for c in tender.award_criteria].count("quality") == 5


def test_criterion_arrays_that_disagree_are_not_paired_at_all() -> None:
    # 2.5% of notices carrying award criteria look like this. A criterion wearing
    # another criterion's weight is plausible enough never to be noticed.
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "award-criterion-type-lot": ["price", "quality", "quality"],
        "award-criterion-number-lot": ["60", "40"],
    }
    tender = map_notice(notice)

    assert tender.criteria_unpaired is True
    assert tender.award_criteria == []
    assert tender.raw["award-criterion-type-lot"] == ["price", "quality", "quality"]


def test_selection_criteria_carry_the_qualification_bar() -> None:
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "selection-criterion-lot": ["tp-abil", "ef-stand"],
        "selection-criterion-description-lot": {"eng": ["Three similar projects", "Turnover"]},
    }
    tender = map_notice(notice)

    assert tender.criteria_unpaired is False
    assert [c.type for c in tender.selection_criteria] == ["tp-abil", "ef-stand"]
    assert tender.selection_criteria[0].description == "Three similar projects"


# ----------------------------------------------- what the wider projection adds


def test_the_submission_language_is_not_the_publication_language() -> None:
    """The register's language column wants the language a bid may be written in.

    They are different facts and the notice states both. 532622-2026 is published
    in Spanish and accepts bids in Catalan or Spanish.
    """
    tender = map_notice(load_ted_fixture("lot_values_and_languages_do_not_match_the_lots"))

    assert tender.languages == ["SPA"]
    assert tender.submission_languages == ["CAT", "SPA"]


def test_the_procedure_and_the_buyers_sector_are_read(competition: dict) -> None:
    tender = map_notice(competition)

    assert tender.procedure_type == competition["procedure-type"]
    assert tender.main_activity == competition["main-activity"][0]


def test_a_contract_length_keeps_its_unit() -> None:
    notice = load_ted_fixture("lots_with_different_deadlines")
    tender = map_notice(notice)

    assert notice["contract-duration-period-lot"][0] == {"value": "10", "unit": "MONTH"}
    # Repeated once per lot in the source; one fact here.
    assert tender.contract_durations == [ContractDuration(value="10", unit="MONTH")]


def test_every_document_link_is_kept_not_only_the_first() -> None:
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "document-url-lot": ["https://a.example", "https://b.example", "https://a.example"],
    }
    tender = map_notice(notice)

    assert tender.document_urls == ["https://a.example", "https://b.example"]
    assert tender.documents_url == "https://a.example", "one link for a template with room for one"


def test_a_single_lot_value_fills_the_notice_value_when_the_procedure_states_none() -> None:
    # 55 notices over 1 July to 11 September read "Not stated" for exactly this.
    # With one lot there is nothing to attribute wrongly.
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "identifier-lot": ["LOT-0001"],
        "estimated-value-lot": ["450000"],
        "estimated-value-cur-lot": ["EUR"],
    }
    tender = map_notice(notice)

    assert tender.estimated_value == Decimal("450000")
    assert tender.currency == "EUR"
    assert tender.estimated_value_source == "estimated-value-lot"


def test_several_lot_values_never_fill_the_notice_value() -> None:
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "identifier-lot": ["LOT-0001", "LOT-0002"],
        "estimated-value-lot": ["100", "200"],
        "estimated-value-cur-lot": ["EUR"],
    }
    tender = map_notice(notice)

    assert tender.estimated_value is None
    assert tender.estimated_value_source is None
    assert tender.lot_values == [Decimal("100"), Decimal("200")]
    assert tender.lot_value_currency == "EUR"


def test_a_procedure_value_is_always_preferred_and_says_so(competition: dict) -> None:
    tender = map_notice(competition)

    if tender.estimated_value is not None:
        assert tender.estimated_value_source == "estimated-value-proc"


def test_identical_lot_values_are_all_kept() -> None:
    # Four lots priced the same are four facts; collapsing them would make a
    # six-lot notice look like a three-lot one.
    tender = map_notice(load_ted_fixture("six_lots_with_unaligned_parallel_arrays"))

    assert len(tender.lot_values) == 6
    assert tender.lot_values.count(Decimal("7000000")) == 4


# ---------------------------------------------------------- shapes and links


def test_both_multilingual_shapes_are_read() -> None:
    notice = load_ted_fixture("description_proc_is_a_string_description_lot_is_a_list")

    assert all(isinstance(v, str) for v in notice["description-proc"].values())
    assert all(isinstance(v, list) for v in notice["description-lot"].values())

    tender = map_notice(notice)
    assert tender.description
    assert any(block.field == "description-lot" for block in tender.screening_blocks)


def test_the_source_url_is_the_english_html_page(competition: dict) -> None:
    tender = map_notice(competition)

    assert tender.source_url == competition["links"]["html"]["ENG"]
    # Language keys in links are UPPER CASE; in the title dictionaries they are not.
    assert "ENG" in competition["links"]["html"]
    assert "eng" in competition["notice-title"]


def test_place_of_performance_keeps_the_codes_as_given() -> None:
    notice = load_ted_fixture("notice_title_in_24_languages")
    tender = map_notice(notice)

    assert tender.place_of_performance is not None
    # NUTS regions and ISO-3 countries arrive mixed in one array; both are kept.
    assert "FR101" in tender.place_of_performance
    assert "FRA" in tender.place_of_performance


def test_the_performance_country_is_separate_from_the_buyers_country() -> None:
    # 619675-2026: a French buyer procuring a waste study for Luanda Province.
    # One country field would lose exactly this notice.
    tender = map_notice(load_ted_fixture("buyer_in_one_country_work_in_another"))

    assert tender.buyer_country == "FRA"
    assert tender.place_of_performance_country == ["AGO"]
    assert tender.crosses_border_from_buyer is True
    assert tender.multi_country is False


def test_the_performance_country_is_deduplicated() -> None:
    # The procedure-level field repeats once per lot: 21 entries, one country.
    notice = load_ted_fixture("six_lots_with_unaligned_parallel_arrays")
    tender = map_notice(notice)

    assert len(notice["place-of-performance-country-proc"]) > 1
    assert tender.place_of_performance_country == ["SWE"]
    assert tender.multi_country is False


def test_several_performance_countries_are_all_kept_and_flagged() -> None:
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "place-of-performance-country-proc": ["MAR", "DZA", "MAR"],
    }
    tender = map_notice(notice)

    assert tender.place_of_performance_country == ["MAR", "DZA"]
    assert tender.multi_country is True


def test_the_lot_level_country_is_read_when_the_procedure_level_one_is_absent() -> None:
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "place-of-performance-country-lot": ["ESP"],
    }

    assert map_notice(notice).place_of_performance_country == ["ESP"]


def test_a_nuts_code_never_reaches_the_country_field() -> None:
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "place-of-performance": ["FR101", "FRA"],
        "place-of-performance-country-proc": ["FRA"],
    }
    tender = map_notice(notice)

    assert tender.place_of_performance_country == ["FRA"]
    assert "FR101" in (tender.place_of_performance or "")


def test_a_country_we_cannot_name_is_still_stored() -> None:
    # A source fact is stored as received; only configured values are validated.
    notice = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "x"},
        "place-of-performance-country-proc": ["ATA"],
    }

    assert map_notice(notice).place_of_performance_country == ["ATA"]


def test_no_performance_country_is_an_empty_list_not_a_guess() -> None:
    tender = map_notice({"publication-number": "1-2026", "notice-title": {"eng": "x"}})

    assert tender.place_of_performance_country == []
    assert tender.multi_country is False
    assert tender.crosses_border_from_buyer is False


def test_the_published_date_is_teds_own_calendar_date() -> None:
    notice = load_ted_fixture("notice_title_in_24_languages")
    tender = map_notice(notice)

    # "2026-06-15+02:00" is a date with an offset, not a moment.
    assert notice["publication-date"] == "2026-06-15+02:00"
    assert tender.published_date == date(2026, 6, 15)


def test_absent_fields_become_none_and_never_a_placeholder() -> None:
    tender = map_notice({"publication-number": "1-2026", "notice-title": {"eng": "x"}})

    assert tender.description is None
    assert tender.buyer_name is None
    assert tender.buyer_country is None
    assert tender.estimated_value is None
    assert tender.documents_url is None
    assert tender.source_version is None
    assert tender.languages == []


def test_the_official_languages_are_kept(competition: dict) -> None:
    tender = map_notice(competition)

    assert tender.languages == competition.get("official-language", [])


def test_every_fixture_maps_without_raising(ted_fixture_name: str) -> None:
    tender = map_notice(load_ted_fixture(ted_fixture_name))

    assert tender.source_id
    assert tender.title
