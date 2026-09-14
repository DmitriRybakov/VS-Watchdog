"""One display vocabulary, and the keys underneath it that never move.

The distinction these tests exist to hold: a **label** is what the team calls a
field and may be changed in one place at any time; a **key** is plumbing that
appears in stored extractions, export columns and URLs, and changing one is a data
migration. A test that asserted only the labels would let a key rename through.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from watchdog.core import vocabulary
from watchdog.core.models import DetailField, TenderDetail
from watchdog.core.vocabulary import FIELDS, FIELDS_EXTRACTED, Group, Provenance

# The keys as they are today. Written out rather than derived, so adding a field
# is a deliberate edit here and renaming one is a failure rather than a silence.
EXPECTED_KEYS = (
    "tender_name",
    "tender_name_native",
    "client",
    "client_country",
    "project_location",
    "published_date",
    "deadline_date",
    "public_platform",
    "phase_of_tender",
    "phase_of_project",
    "scope_of_work_technical",
    "scope_of_work_commercial",
    "duration",
    "budget_and_commercial_model",
    "professional_indemnity_requirement",
    "local_content_requirement",
    "site_visit_requirement",
    "submission_language",
    "team_composition_requirement",
    "experience_qualification_requirement",
    "evaluation_and_award_criteria",
    "procurement_documents_link",
    "documents_likely_required",
)


def test_the_keys_are_stable_and_unique() -> None:
    keys = tuple(spec.key for spec in FIELDS)

    assert keys == EXPECTED_KEYS
    assert len(set(keys)) == len(keys)


def test_the_labels_are_the_teams_words_not_the_platforms() -> None:
    """TED says buyer and country of performance. Our register does not."""
    assert vocabulary.label("client") == "Client"
    assert vocabulary.label("client_country") == "Client Country"
    assert vocabulary.label("public_platform") == "Public Platform"
    assert vocabulary.label("phase_of_tender") == "Phase of Tender"
    assert vocabulary.label("tender_name") == "Tender Name"
    assert vocabulary.label("procurement_documents_link") == "Procurement Documents Link"


def test_a_label_that_needed_a_sentence_beside_it_was_the_wrong_label() -> None:
    """Where the work happens is not who is buying, and the label has to say so itself."""
    assert vocabulary.label("project_location") == "Execution location"
    assert vocabulary.spec("project_location").note is None

    assert vocabulary.label("tender_name_native") == "Untranslated title"


def test_the_language_field_is_labelled_submission_language() -> None:
    """It is the language a bid may be written in, and nothing else.

    Never the notice's publication language: a buyer that publishes in Dutch and
    accepts English bids would otherwise be shown as requiring Dutch, and nothing
    on the page would flag it.
    """
    spec = vocabulary.spec("submission_language")

    assert spec is not None
    assert spec.label == "Submission language"
    assert "published in" in (spec.note or "")


def test_public_platform_is_not_the_bidding_portal_or_the_documents() -> None:
    spec = vocabulary.spec("public_platform")

    assert spec is not None
    assert "bidding portal" in (spec.note or "")


def test_an_unknown_key_falls_back_to_itself_rather_than_inventing_a_label() -> None:
    assert vocabulary.label("not_a_field") == "not_a_field"


def test_every_field_belongs_to_one_of_the_two_register_groups() -> None:
    documents = vocabulary.in_group(Group.DOCUMENTS)
    analysis = vocabulary.in_group(Group.ANALYSIS)

    assert len(documents) + len(analysis) == len(FIELDS)
    assert {spec.key for spec in analysis} == {
        "phase_of_project",
        "scope_of_work_technical",
        "scope_of_work_commercial",
        "documents_likely_required",
    }


def test_groups_keep_the_workbook_order() -> None:
    """Grouping partitions the list; it never reorders it."""
    order = [spec.key for spec in FIELDS]
    inside = [spec.key for spec in vocabulary.in_group(Group.DOCUMENTS)]

    assert inside == [key for key in order if key in set(inside)]


def test_the_extraction_columns_are_the_ones_no_api_can_fill() -> None:
    assert set(FIELDS_EXTRACTED) == {
        "scope_of_work_technical",
        "scope_of_work_commercial",
        "professional_indemnity_requirement",
        "local_content_requirement",
        "site_visit_requirement",
        "team_composition_requirement",
        "documents_likely_required",
    }


def test_every_field_declares_a_provenance() -> None:
    for spec in FIELDS:
        assert isinstance(spec.provenance, Provenance)


def test_a_stored_extraction_refuses_a_key_that_is_not_a_register_column() -> None:
    """docs/decisions/0001: a renamed field is now an error, not a blank.

    A blank looks exactly like a notice that said nothing, so the page reads as
    complete while a fact is silently missing.
    """
    with pytest.raises(ValidationError, match="unknown extracted field"):
        TenderDetail(
            tender_id="ted:1-2026",
            fields={"scope": DetailField(value="Pre-FEED")},
        )


def test_a_stored_extraction_accepts_every_register_extraction_column() -> None:
    detail = TenderDetail(
        tender_id="ted:1-2026",
        fields={key: DetailField(value=None) for key in FIELDS_EXTRACTED},
    )

    assert set(detail.fields) == set(FIELDS_EXTRACTED)
