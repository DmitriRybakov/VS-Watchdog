"""The domain model's promises: identity, unknowns, UTC and score bounds."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from watchdog.core.enums import Band, ContractNature, Domain, SourcePlatform
from watchdog.core.models import (
    AxisScore,
    RulesResult,
    ScreeningResult,
    Tender,
    TextBlock,
    content_hash,
    make_tender_id,
)
from watchdog.storage.repository import SOURCE_FIELDS


def test_id_is_derived_from_source_and_source_id(make_tender) -> None:
    tender = make_tender("00123456-2026")

    assert tender.id == "ted:00123456-2026"
    assert tender.id == make_tender_id(SourcePlatform.TED, "00123456-2026")


def test_screening_text_is_every_labelled_block(make_tender) -> None:
    tender = make_tender(title_native="Hydrogen study", description="Pre-FEED.")

    assert tender.screening_text == (
        "[title-proc/eng]\nHydrogen study\n\n[description-proc/eng]\nPre-FEED."
    )


def test_screening_text_never_contains_the_composed_display_title(make_tender) -> None:
    tender = make_tender(
        title="Norway - Feasibility study, advisory service, analysis - Hydrogen study",
        title_native="Hydrogen study",
    )

    # The CPV label in the composed title is the code we filtered on, restated.
    assert "Feasibility study, advisory service, analysis" not in tender.screening_text


def test_a_notice_with_no_native_title_is_screened_on_its_description(make_tender) -> None:
    tender = make_tender(title_native=None, description="Pre-FEED.")

    assert tender.screening_text == "[description-proc/eng]\nPre-FEED."


def test_screening_text_of_a_notice_with_no_original_text_is_empty(make_tender) -> None:
    tender = make_tender(title_native=None, description=None)

    # Thin text routes to human review; it is never evidence of irrelevance.
    assert tender.screening_text == ""


def test_content_hash_changes_when_the_notice_is_corrected(make_tender) -> None:
    original = make_tender()
    corrected = original.model_copy(
        update={
            "screening_blocks": [
                TextBlock(field="title-proc", language="eng", text="Feasibility study, corrected")
            ]
        }
    )

    assert original.content_hash != corrected.content_hash


def test_content_hash_ignores_the_order_of_cpv_codes() -> None:
    first = content_hash("text", ["73210000", "09000000"])
    second = content_hash("text", ["09000000", "73210000"])

    assert first == second


def test_content_hash_changes_when_a_cpv_code_is_added() -> None:
    assert content_hash("text", ["71241000"]) != content_hash("text", ["71241000", "09330000"])


def test_no_screening_text_and_empty_screening_text_hash_differently() -> None:
    # An unknown fact is None, never "". The hash has to keep them apart.
    assert content_hash("", []) == content_hash("", [])
    assert content_hash("", []) != content_hash(" ", [])


def test_a_hash_covering_one_language_would_miss_a_corrected_variant(make_tender) -> None:
    dutch = TextBlock(field="title-proc", language="nld", text="Haalbaarheidsstudie")
    french = TextBlock(field="title-proc", language="fra", text="Etude de faisabilite")

    tender = make_tender().model_copy(update={"screening_blocks": [dutch, french]})
    corrected = tender.model_copy(
        update={
            "screening_blocks": [
                dutch,
                TextBlock(
                    field="title-proc", language="fra", text="Etude de faisabilite, corrigee"
                ),
            ]
        }
    )

    assert tender.content_hash != corrected.content_hash


def test_a_deadline_date_without_a_time_leaves_the_moment_null(make_tender) -> None:
    tender = make_tender(deadline=None, deadline_date=date(2026, 4, 15))

    assert tender.deadline is None
    assert tender.deadline_date == date(2026, 4, 15)


def test_a_notice_mentioning_services_anywhere_counts_as_services(make_tender) -> None:
    tender = make_tender(contract_natures=[ContractNature.WORKS, ContractNature.SERVICES])
    tender = tender.model_copy(update={"contract_nature": ContractNature.WORKS})

    assert tender.contract_nature is ContractNature.WORKS
    assert tender.is_services is True


def test_a_works_only_notice_is_not_services(make_tender) -> None:
    tender = make_tender(contract_natures=[ContractNature.WORKS])

    assert tender.is_services is False


def test_a_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Tender(
            source=SourcePlatform.TED,
            source_id="1",
            title="x",
            deadline=datetime(2026, 4, 15, 12, 0),
        )


def test_a_local_timestamp_is_converted_to_utc(make_tender) -> None:
    oslo_noon = datetime(2026, 4, 15, 12, 0, tzinfo=timezone(timedelta(hours=2)))

    tender = make_tender(deadline=oslo_noon)

    assert tender.deadline == datetime(2026, 4, 15, 10, 0, tzinfo=UTC)


def test_a_publication_date_is_a_calendar_date(make_tender) -> None:
    tender = make_tender(published_date=date(2026, 3, 1))

    assert tender.published_date == date(2026, 3, 1)
    assert not isinstance(tender.published_date, datetime)


def test_a_moment_in_time_is_not_accepted_as_a_publication_date(make_tender) -> None:
    # A source handing us 13:30 means the mapping is wrong; truncating it would
    # hide that and keep the invented precision out of sight.
    with pytest.raises(ValidationError):
        make_tender(published_date=datetime(2026, 3, 1, 13, 30, tzinfo=UTC))


def test_an_axis_score_may_be_unknown() -> None:
    axis = AxisScore[Domain](score=None, label=Domain.UNKNOWN)

    assert axis.score is None


def test_an_axis_score_above_five_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AxisScore[Domain](score=6, label=Domain.HYDROGEN)


def test_a_final_score_of_zero_is_rejected() -> None:
    # The final score is 1-5; 0 is not a band, it is a bug.
    with pytest.raises(ValidationError):
        ScreeningResult(
            tender_id="ted:1",
            score=0,
            band=Band.ARCHIVE,
            confidence=0.5,
            rules=RulesResult(tender_id="ted:1", rules_version="r1"),
            explanation="",
            screened_content_hash="abc",
            provider="disabled",
            rules_version="r1",
            policy_version="p1",
            profile_version="f1",
        )


def test_every_tender_field_is_either_a_source_field_or_ours() -> None:
    # If a field is added to Tender and forgotten in the upsert, it would silently
    # never be updated. This is the tripwire for that.
    ours = {"first_seen_at", "last_seen_at"}

    assert set(Tender.model_fields) == set(SOURCE_FIELDS) | ours
