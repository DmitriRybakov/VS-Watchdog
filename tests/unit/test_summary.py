"""The detail page's own logic: what each field says, and what each match did.

Four claims these tests hold, all of them things the page got wrong before:

- an exclusion is never listed beside a subject-matter rule in the same style,
  and one that changed no outcome says so;
- a criterion's name is never shown against a number nothing associates it with;
- a selection-criterion code reads as a requirement, not as ``slc-stand-other``;
- an empty field says which kind of nothing it is.
"""

from __future__ import annotations

from datetime import date

from watchdog.core.enums import (
    Band,
    Domain,
    Polarity,
    RuleSignal,
    RulesRoute,
    RuleStrength,
)
from watchdog.core.models import (
    AwardCriterion,
    RegisterRow,
    RuleMatch,
    RulesResult,
    ScreeningResult,
    SelectionCriterion,
    Tender,
    TenderDetail,
)
from watchdog.core.vocabulary import Group, Provenance
from watchdog.services import summary as summary_service


def _match(rule_id: str, signal: RuleSignal, strength: RuleStrength) -> RuleMatch:
    return RuleMatch(
        rule_id=rule_id,
        signal=signal,
        strength=strength,
        alias_matched="hydrogen",
        field="description-proc",
        evidence="...a hydrogen production facility...",
        polarity=Polarity.NEGATIVE if signal is RuleSignal.EXCLUSION else Polarity.POSITIVE,
    )


def _row(
    tender: Tender,
    *,
    matches: list[RuleMatch] | None = None,
    domains: list[Domain] | None = None,
    route: RulesRoute = RulesRoute.ASSESS,
    cpv_match: bool = False,
) -> RegisterRow:
    found = matches or []
    result = ScreeningResult(
        tender_id=tender.id,
        score=3,
        band=Band.REVIEW,
        rules_only_score=2,
        confidence=0.5,
        rules=RulesResult(
            tender_id=tender.id,
            matches=found,
            domains_hit=domains or [],
            cpv_match=cpv_match,
            has_exclusion=any(item.signal is RuleSignal.EXCLUSION for item in found),
            route=route,
            rules_version="3",
        ),
        explanation="Keyword evidence only.",
        screened_content_hash="x" * 64,
        provider="disabled",
        rules_version="3",
        policy_version="1",
        profile_version="1",
    )
    return RegisterRow(tender=tender, screening=result, days_left=10)


def _field(built: summary_service.NoticeSummary, key: str) -> summary_service.FieldView:
    for group in built.groups:
        for item in group.fields:
            if item.key == key:
                return item
    raise AssertionError(f"no field {key!r} on the page")


# ------------------------------------------------------------- matched rules


def test_an_exclusion_that_changed_nothing_is_not_shown_as_evidence(make_tender) -> None:
    """604181-2026: the exclusions fired and the notice still went to assessment.

    Listed in the same style as the domain rules, they read as part of why the
    notice scored what it did - so a notice appears relevant partly because it is
    not social welfare.
    """
    row = _row(
        make_tender(),
        matches=[
            _match("domain_hydrogen", RuleSignal.DOMAIN, RuleStrength.HIGH),
            _match("exclusion_social_welfare", RuleSignal.EXCLUSION, RuleStrength.MEDIUM),
        ],
        domains=[Domain.HYDROGEN],
        route=RulesRoute.ASSESS,
    )

    groups = {group.key: group for group in summary_service.rule_groups(row)}

    assert set(groups) == {"domain", "exclusion"}
    assert groups["domain"].matches[0].rule_id == "domain_hydrogen"
    assert "changed nothing" in groups["exclusion"].outcome
    assert "subject-matter rule also matched" in groups["exclusion"].outcome
    assert "not because they count towards the score" in groups["exclusion"].outcome


def test_an_exclusion_that_archived_the_notice_says_that_instead(make_tender) -> None:
    row = _row(
        make_tender(),
        matches=[_match("exclusion_social_welfare", RuleSignal.EXCLUSION, RuleStrength.MEDIUM)],
        domains=[],
        route=RulesRoute.ARCHIVE_CANDIDATE,
    )

    groups = {group.key: group for group in summary_service.rule_groups(row)}

    assert "archive candidate list" in groups["exclusion"].outcome
    assert "never deleted" in groups["exclusion"].outcome


def test_a_cpv_match_is_named_as_the_reason_an_exclusion_did_not_archive(make_tender) -> None:
    row = _row(
        make_tender(),
        matches=[_match("exclusion_building", RuleSignal.EXCLUSION, RuleStrength.MEDIUM)],
        domains=[],
        route=RulesRoute.ASSESS,
        cpv_match=True,
    )

    groups = {group.key: group for group in summary_service.rule_groups(row)}

    assert "CPV code on the archive guard list" in groups["exclusion"].outcome


def test_supporting_terms_are_kept_apart_and_say_they_established_nothing(make_tender) -> None:
    row = _row(
        make_tender(),
        matches=[
            _match("domain_energy_generic", RuleSignal.DOMAIN, RuleStrength.SUPPORTING),
            _match("activity_advisory", RuleSignal.ACTIVITY, RuleStrength.MEDIUM),
        ],
        domains=[],
    )

    groups = {group.key: group for group in summary_service.rule_groups(row)}

    assert set(groups) == {"activity", "supporting"}
    assert "established nothing" in groups["supporting"].heading
    assert "establishes no subject matter" in groups["activity"].outcome


def test_a_notice_that_was_never_screened_has_no_rule_groups(make_tender) -> None:
    row = RegisterRow(tender=make_tender(), screening=None)

    assert summary_service.rule_groups(row) == ()


# ------------------------------------------------------------------ criteria


def test_an_award_criterion_is_never_shown_against_a_number(make_tender) -> None:
    """Equal array lengths do not prove matching order, on one lot or on five.

    A wrong weight against the right criterion name is the kind of error a
    colleague acts on and nobody catches.
    """
    tender = make_tender().model_copy(
        update={
            "award_criteria": [
                AwardCriterion(type="cost", name="Honorar", number="40", number_kind="per-exa"),
                AwardCriterion(
                    type="quality", name="Qualifikation", number="20", number_kind="per-exa"
                ),
            ]
        }
    )

    field = _field(summary_service.summarise(_row(tender)), "evaluation_and_award_criteria")
    texts = [value.text for value in field.values]

    assert "Honorar" in texts
    assert "Qualifikation" in texts
    assert "40%, 20%" in texts, "the numbers are their own line, not attached to a name"
    assert not any("Honorar 40" in text for text in texts)
    assert field.caution is not None
    assert "states no ordering" in field.caution
    assert "single-lot notice either" in field.caution


def test_criteria_that_could_not_be_read_as_one_list_say_so(make_tender) -> None:
    tender = make_tender().model_copy(
        update={
            "criteria_unpaired": True,
            "award_criteria": [],
        }
    )

    field = _field(summary_service.summarise(_row(tender)), "evaluation_and_award_criteria")

    assert not field.filled
    assert field.caution is not None
    assert "disagree in length" in field.caution


def test_a_selection_criterion_code_is_shown_as_what_it_means(make_tender) -> None:
    tender = make_tender().model_copy(
        update={
            "selection_criteria": [
                SelectionCriterion(type="slc-abil-ref-services", description="Three references"),
                SelectionCriterion(type="slc-stand-other", description="Minimum turnover"),
            ]
        }
    )

    field = _field(summary_service.summarise(_row(tender)), "experience_qualification_requirement")
    texts = [value.text for value in field.values]

    assert "Reference projects: services delivered" in texts
    assert "Other financial standing requirement" in texts
    assert field.caution is not None
    assert "no description is shown against a code" in field.caution


def test_a_selection_criterion_code_we_do_not_know_falls_back_to_its_category(
    make_tender,
) -> None:
    tender = make_tender().model_copy(
        update={"selection_criteria": [SelectionCriterion(type="slc-abil-brand-new")]}
    )

    field = _field(summary_service.summarise(_row(tender)), "experience_qualification_requirement")

    assert field.values[0].text == "Technical and professional ability"
    assert "slc-abil-brand-new" in (field.values[0].note or "")


def test_professional_indemnity_is_read_from_the_code_not_from_a_paired_description(
    make_tender,
) -> None:
    """The presence of the code is a fact on its own.

    It does not depend on which description TED sent it beside, and that
    association is not established.
    """
    tender = make_tender().model_copy(
        update={"selection_criteria": [SelectionCriterion(type="slc-stand-ins")]}
    )

    field = _field(summary_service.summarise(_row(tender)), "professional_indemnity_requirement")

    assert field.filled
    assert field.provenance is Provenance.SOURCE


# -------------------------------------------------------------------- fields


def test_every_register_field_appears_even_when_nothing_fills_it(make_tender) -> None:
    built = summary_service.summarise(_row(make_tender()))
    keys = [item.key for group in built.groups for item in group.fields]

    assert "local_content_requirement" in keys
    assert "documents_likely_required" in keys
    assert len(keys) == len(set(keys))


def test_an_empty_field_says_which_kind_of_nothing_it_is(make_tender) -> None:
    built = summary_service.summarise(_row(make_tender()))

    # Nothing has read a document, so this is "not retrieved", not "the platform
    # sent no value".
    assert _field(built, "local_content_requirement").absent == summary_service.ABSENT_RETRIEVAL
    # Nothing has judged it, so this is "no assessment".
    assert _field(built, "scope_of_work_technical").absent == summary_service.ABSENT_ASSESSMENT


def test_phase_of_project_is_empty_and_labelled_an_assessment_until_ai_is_on(
    make_tender,
) -> None:
    field = _field(summary_service.summarise(_row(make_tender())), "phase_of_project")

    assert not field.filled
    assert field.group is Group.ANALYSIS
    assert field.provenance is Provenance.ASSESSMENT


def test_phase_of_project_says_document_when_the_extraction_recorded_where_it_read_it(
    make_tender,
) -> None:
    """Correction F: it can be stated in the source as well as inferred."""
    from watchdog.core.models import DetailField

    tender = make_tender()
    detail = TenderDetail(
        tender_id=tender.id,
        fields={
            "scope_of_work_technical": DetailField(
                value="Pre-FEED for a 100 MW electrolyser", source_ref="section 2.1"
            )
        },
    )

    field = _field(
        summary_service.summarise(_row(tender), detail=detail), "scope_of_work_technical"
    )

    assert field.filled
    assert field.provenance is Provenance.DOCUMENT
    assert "section 2.1" in (field.values[0].note or "")


def test_the_submission_language_never_borrows_the_publication_language(make_tender) -> None:
    """A buyer publishing in Dutch and accepting English bids must not read as Dutch."""
    tender = make_tender().model_copy(
        update={"languages": ["NLD"], "submission_languages": ["ENG"]}
    )

    field = _field(summary_service.summarise(_row(tender)), "submission_language")

    assert field.values[0].text == "English"
    assert "published in: Dutch" in (field.values[0].note or "")


def test_a_deadline_with_a_time_keeps_its_converted_date_and_time_together(make_tender) -> None:
    """Around midnight the buyer's local date and the UTC time are different days."""
    field = _field(summary_service.summarise(_row(make_tender())), "deadline_date")

    assert field.values[0].text == "2026-04-15 12:00 UTC"


def test_a_deadline_of_a_date_only_is_not_converted_to_utc(make_tender) -> None:
    tender = make_tender().model_copy(update={"deadline": None, "deadline_date": date(2026, 4, 15)})

    field = _field(summary_service.summarise(_row(tender)), "deadline_date")

    assert field.values[0].text == "2026-04-15"
    assert "none has been invented" in (field.values[0].note or "")
    assert "UTC" not in field.values[0].text


def test_work_outside_the_clients_country_is_stated_rather_than_left_to_be_inferred(
    make_tender,
) -> None:
    tender = make_tender(buyer_country="FRA", performance_countries=["AGO"])

    field = _field(summary_service.summarise(_row(tender)), "project_location")

    assert field.caution is not None
    assert "Work outside the client's country: yes" in field.caution
    assert "France" in field.caution
    assert "Angola" in field.caution


def test_a_page_can_be_built_for_a_notice_nothing_has_ever_screened(make_tender) -> None:
    built = summary_service.summarise(RegisterRow(tender=make_tender()))

    assert built.rule_groups == ()
    assert _field(built, "client").filled
