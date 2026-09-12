"""The domain model's promises: identity, unknowns, UTC and score bounds."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from watchdog.core.enums import Band, Domain, SourcePlatform
from watchdog.core.models import (
    AxisScore,
    RulesResult,
    ScreeningResult,
    Tender,
    content_hash,
    make_tender_id,
)
from watchdog.storage.repository import SOURCE_FIELDS


def test_id_is_derived_from_source_and_source_id(make_tender) -> None:
    tender = make_tender("00123456-2026")

    assert tender.id == "ted:00123456-2026"
    assert tender.id == make_tender_id(SourcePlatform.TED, "00123456-2026")


def test_screening_text_is_title_and_description(make_tender) -> None:
    tender = make_tender(title="Hydrogen study", description="Pre-FEED.")

    assert tender.screening_text == "Hydrogen study\n\nPre-FEED."


def test_screening_text_of_a_thin_notice_is_just_the_title(make_tender) -> None:
    tender = make_tender(title="Hydrogen study", description=None)

    assert tender.screening_text == "Hydrogen study"


def test_content_hash_changes_when_the_notice_is_corrected(make_tender) -> None:
    original = make_tender()
    corrected = original.model_copy(update={"title": "Feasibility study, corrected"})

    assert original.content_hash != corrected.content_hash


def test_content_hash_ignores_the_order_of_additional_cpv_codes() -> None:
    first = content_hash("t", "d", "71241000", ["73210000", "09000000"])
    second = content_hash("t", "d", "71241000", ["09000000", "73210000"])

    assert first == second


def test_a_missing_description_and_an_empty_one_hash_differently() -> None:
    # An unknown fact is None, never "". The hash has to keep them apart.
    assert content_hash("t", None, None, []) != content_hash("t", "", None, [])


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
