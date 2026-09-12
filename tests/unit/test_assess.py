"""The assessment stage, driven entirely by the fake provider. No network, no model.

What these check is not whether the model is clever. It is that a batch survives
a bad answer, that a cache hit costs nothing, that the switched-off path is clean,
and that the prompt carries what it should and leaves out what must never reach it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from watchdog.core.enums import DecisionStage, Domain, RulesRoute, ServiceType
from watchdog.core.models import Assessment, RulesResult, Tender, TextBlock
from watchdog.llm.cache import ResponseCache
from watchdog.llm.disabled import DisabledProvider
from watchdog.llm.fake import FakeProvider
from watchdog.llm.provider import LLMCallError
from watchdog.screening.assess import (
    TRUNCATION_MARKER,
    Candidate,
    assess,
    build_notice_text,
    check_quotes_are_grounded,
    confidence_for,
)
from watchdog.screening.profile import Profile
from watchdog.screening.prompts import DEFAULT_PROMPT_VERSION, render_system_prompt

TenderFactory = Callable[..., Tender]

# Both occur in the notice text the default tender produces.
GROUNDED_DOMAIN_QUOTE = "hydrogen production facility"
GROUNDED_STUDY_QUOTE = "Pre-FEED study"


def _answer(
    *,
    domain: int | None = 5,
    quote: bool = True,
    quotes: list[str] | None = None,
    missing: list[str] | None = None,
) -> str:
    """One canned answer, as raw JSON.

    Built as a mapping rather than through ``Assessment``, so that a test can
    produce an answer the schema refuses - which is the point of several of them.
    """
    evidence = quotes if quotes is not None else [GROUNDED_DOMAIN_QUOTE]
    return json.dumps(
        {
            "domain_fit": {
                "score": domain,
                "label": Domain.HYDROGEN.value if domain is not None else Domain.UNKNOWN.value,
                "evidence": evidence if domain is not None and quote else [],
            },
            "service_fit": {
                "score": 5,
                "label": ServiceType.EARLY_PHASE_STUDY.value,
                "evidence": [GROUNDED_STUDY_QUOTE],
            },
            "stage_fit": {
                "score": 5,
                "label": DecisionStage.PRE_FEED_FEASIBILITY.value,
                "evidence": [GROUNDED_STUDY_QUOTE],
            },
            "negative_signals": [],
            "missing_information": missing or [],
            "short_reason": "Pre-FEED study for hydrogen production.",
        }
    )


def _candidate(tender: Tender, *, route: RulesRoute = RulesRoute.ASSESS) -> Candidate:
    return Candidate(
        tender=tender,
        rules=RulesResult(tender_id=tender.id, route=route, rules_version="1"),
    )


# --------------------------------------------------------------- switched off


def test_the_disabled_provider_assesses_nothing_and_does_not_raise(
    make_tender: TenderFactory, profile: Profile
) -> None:
    candidates = [_candidate(make_tender("1-2026")), _candidate(make_tender("2-2026"))]

    run = assess(candidates, provider=DisabledProvider(), profile=profile)

    assert run.ai_enabled is False
    assert run.results == ()
    assert len(run.skipped) == 2
    assert run.tokens_in == 0


# ------------------------------------------------------------------- routing


def test_only_notices_routed_to_assess_reach_the_model(
    make_tender: TenderFactory, profile: Profile
) -> None:
    provider = FakeProvider(default=_answer())
    candidates = [
        _candidate(make_tender("1-2026")),
        _candidate(make_tender("2-2026"), route=RulesRoute.ARCHIVE_CANDIDATE),
    ]

    run = assess(candidates, provider=provider, profile=profile)

    assert provider.call_count == 1
    assert [item.tender_id for item in run.results] == ["ted:1-2026"]
    assert run.skipped == ("ted:2-2026",)


# ------------------------------------------------------- one failure, one batch


def test_one_failing_notice_leaves_the_rest_of_the_batch_assessed(
    make_tender: TenderFactory, profile: Profile
) -> None:
    provider = FakeProvider(
        responses={"BROKEN": LLMCallError("the model could not be reached")},
        default=_answer(),
    )
    candidates = [
        _candidate(make_tender("1-2026")),
        _candidate(make_tender("2-2026", title_native="BROKEN notice", description=None)),
        _candidate(make_tender("3-2026")),
    ]

    run = assess(candidates, provider=provider, profile=profile)

    assert len(run.results) == 3
    assert [item.tender_id for item in run.assessed] == ["ted:1-2026", "ted:3-2026"]
    assert [item.tender_id for item in run.failed] == ["ted:2-2026"]
    assert run.errors == ("ted:2-2026: the model could not be reached",)
    assert run.counts["failed"] == 1


def test_an_unrepairable_answer_leaves_that_notice_unassessed(
    make_tender: TenderFactory, profile: Profile
) -> None:
    provider = FakeProvider(default=["not json at all", "still not json"])

    run = assess([_candidate(make_tender("1-2026"))], provider=provider, profile=profile)

    assert run.assessed == ()
    failure = run.failed[0]
    assert failure.error_type == "LLMOutputError"
    assert failure.assessment is None
    assert failure.confidence == 0.0


def test_a_notice_with_no_text_is_never_sent_and_never_called_irrelevant(
    make_tender: TenderFactory, profile: Profile
) -> None:
    provider = FakeProvider(default=_answer())
    empty = make_tender("1-2026", title_native=None, description=None)

    run = assess([_candidate(empty)], provider=provider, profile=profile)

    assert provider.call_count == 0
    assert run.failed[0].error_type == "NoNoticeText"


# --------------------------------------------------------------------- cache


def test_a_cache_hit_skips_the_call_entirely(
    make_tender: TenderFactory, profile: Profile, tmp_path: Path
) -> None:
    cache = ResponseCache(tmp_path)
    candidates = [_candidate(make_tender("1-2026"))]

    first = FakeProvider(default=_answer())
    assess(candidates, provider=first, profile=profile, cache=cache)
    assert first.call_count == 1

    second = FakeProvider(default=_answer())
    run = assess(candidates, provider=second, profile=profile, cache=cache)

    assert second.call_count == 0
    assert run.results[0].cached is True
    assert run.results[0].assessment is not None
    assert run.tokens_in == 0
    assert run.counts["cached"] == 1


def test_no_cache_calls_the_model_again(
    make_tender: TenderFactory, profile: Profile, tmp_path: Path
) -> None:
    candidates = [_candidate(make_tender("1-2026"))]
    assess(
        candidates,
        provider=FakeProvider(default=_answer()),
        profile=profile,
        cache=ResponseCache(tmp_path),
    )

    provider = FakeProvider(default=_answer())
    assess(
        candidates,
        provider=provider,
        profile=profile,
        cache=ResponseCache(tmp_path, enabled=False),
    )

    assert provider.call_count == 1


def test_a_different_model_does_not_get_the_previous_models_answer(
    make_tender: TenderFactory, profile: Profile, tmp_path: Path
) -> None:
    cache = ResponseCache(tmp_path)
    candidates = [_candidate(make_tender("1-2026"))]
    assess(
        candidates,
        provider=FakeProvider(model="fake-1", default=_answer()),
        profile=profile,
        cache=cache,
    )

    provider = FakeProvider(model="fake-2", default=_answer())
    assess(candidates, provider=provider, profile=profile, cache=cache)

    assert provider.call_count == 1


def test_an_edited_mandate_does_not_serve_the_answer_made_under_the_old_one(
    make_tender: TenderFactory, profile: Profile, tmp_path: Path
) -> None:
    cache = ResponseCache(tmp_path)
    candidates = [_candidate(make_tender("1-2026"))]
    assess(candidates, provider=FakeProvider(default=_answer()), profile=profile, cache=cache)

    edited = profile.model_copy(
        update={
            "screening_mandate": {
                **profile.screening_mandate,
                "identity": "A different statement of what the team is for.",
            }
        }
    )
    provider = FakeProvider(default=_answer())
    assess(candidates, provider=provider, profile=edited, cache=cache)

    assert provider.call_count == 1


# -------------------------------------------------------------- what is sent


def test_the_notice_carries_the_buyers_words_and_not_the_composed_title(
    make_tender: TenderFactory,
) -> None:
    tender = make_tender()

    text, truncated = build_notice_text(tender)

    assert "Feasibility study for a hydrogen production facility" in text
    assert "Pre-FEED study including techno-economic analysis." in text
    assert tender.title not in text
    assert truncated is False


def test_geography_value_and_deadline_never_reach_the_model(
    make_tender: TenderFactory,
) -> None:
    text, _ = build_notice_text(make_tender())

    for forbidden in ("Statsbygg", "250000", "NOK", "2026-04-15", "ted.europa.eu", "71241000"):
        assert forbidden not in text, forbidden


def test_the_title_survives_a_budget_the_description_cannot_fit(
    make_tender: TenderFactory,
) -> None:
    tender = make_tender().model_copy(
        update={
            "screening_blocks": [
                TextBlock(field="title-proc", language="nor", text="Konseptstudie for hydrogen"),
                TextBlock(field="description-proc", language="nor", text="lang beskrivelse " * 200),
            ]
        }
    )

    text, truncated = build_notice_text(tender, budget=400)

    assert "Konseptstudie for hydrogen" in text
    assert truncated is True
    assert len(text) <= 400
    if "lang beskrivelse" in text:
        assert TRUNCATION_MARKER in text


# ---------------------------------------------------------------- confidence


def test_an_unestablished_axis_lowers_confidence_and_says_why(
    make_tender: TenderFactory, profile: Profile
) -> None:
    provider = FakeProvider(default=_answer(domain=None))

    run = assess([_candidate(make_tender("1-2026"))], provider=provider, profile=profile)

    outcome = run.results[0]
    assert outcome.confidence < 1.0
    assert any(
        "Domain fit could not be established" in reason for reason in outcome.confidence_reasons
    )


def test_a_fully_evidenced_assessment_is_fully_confident() -> None:
    assessment = Assessment.model_validate_json(_answer())

    confidence, reasons = confidence_for(
        assessment, notice_chars=2000, truncated=False, cached=False
    )

    assert confidence == 1.0
    assert reasons == ("All three judgements were made and each one is quoted from the notice",)


def test_thin_text_lowers_confidence_rather_than_the_score() -> None:
    assessment = Assessment.model_validate_json(_answer())

    confidence, reasons = confidence_for(
        assessment, notice_chars=120, truncated=False, cached=False
    )

    assert confidence < 1.0
    assert any("short" in reason for reason in reasons)


# ------------------------------------------------------------------ evidence


def test_a_scored_axis_without_a_quote_fails_validation() -> None:
    with pytest.raises(ValidationError, match="domain_fit"):
        Assessment.model_validate_json(_answer(quote=False))


def test_a_scored_axis_without_a_quote_is_repaired_rather_than_scored(
    make_tender: TenderFactory, profile: Profile
) -> None:
    provider = FakeProvider(default=[_answer(quote=False), _answer()])

    run = assess([_candidate(make_tender("1-2026"))], provider=provider, profile=profile)

    assert provider.call_count == 2
    assert run.counts["assessed"] == 1
    assert run.counts["repaired"] == 1
    assert "evidence quote" in provider.calls[1].user


def test_an_invented_quote_is_repaired_rather_than_shown_as_evidence(
    make_tender: TenderFactory, profile: Profile
) -> None:
    invented = _answer(quotes=["a floating wind farm off the coast of Bergen"])
    provider = FakeProvider(default=[invented, _answer()])

    run = assess([_candidate(make_tender("1-2026"))], provider=provider, profile=profile)

    assert provider.call_count == 2
    assert run.counts["assessed"] == 1
    assert "do not occur in the notice text" in provider.calls[1].user
    assert "floating wind farm" in provider.calls[1].user


def test_an_invented_quote_that_is_not_repaired_leaves_the_notice_unassessed(
    make_tender: TenderFactory, profile: Profile
) -> None:
    invented = _answer(quotes=["a floating wind farm off the coast of Bergen"])
    provider = FakeProvider(default=[invented, invented])

    run = assess([_candidate(make_tender("1-2026"))], provider=provider, profile=profile)

    assert run.assessed == ()
    assert run.failed[0].error_type == "LLMOutputError"


def test_a_quote_survives_tidied_case_accents_and_spacing(make_tender: TenderFactory) -> None:
    notice, _ = build_notice_text(make_tender())
    assessment = Assessment.model_validate_json(
        _answer(quotes=["PRE-FEED   study including techno economic analysis"])
    )

    check_quotes_are_grounded(assessment, notice=notice)


def test_a_quote_abbreviated_with_an_ellipsis_is_still_grounded(
    make_tender: TenderFactory,
) -> None:
    notice, _ = build_notice_text(make_tender())
    assessment = Assessment.model_validate_json(
        _answer(quotes=["Pre-FEED study ... techno-economic analysis"])
    )

    check_quotes_are_grounded(assessment, notice=notice)


def test_a_quote_from_the_part_we_truncated_away_is_not_grounded(
    make_tender: TenderFactory,
) -> None:
    assessment = Assessment.model_validate_json(_answer(quotes=["Pre-FEED study"]))

    with pytest.raises(ValueError, match="do not occur"):
        check_quotes_are_grounded(assessment, notice="NOTICE\n\n[title-proc/eng]\nSomething else")


# --------------------------------------------------------------- the prompt


def test_the_prompt_sends_part_one_of_the_profile_and_never_part_two(
    profile: Profile,
) -> None:
    system = render_system_prompt(mandate=profile.mandate_text())

    assert "Entr Advisory & Decision Support" in system
    assert "Relevance and bidability are assessed separately." in system
    for forbidden in (
        "commercial_reality",
        "minimum_economic_bid",
        "local_presence",
        "Nordics, UK and selected Northwest European markets",
    ):
        assert forbidden not in system, forbidden


def test_the_prompt_lists_the_labels_the_code_will_validate(profile: Profile) -> None:
    system = render_system_prompt(mandate=profile.mandate_text())

    for member in (Domain.CCS_CO2, ServiceType.ENERGY_MODELLING, DecisionStage.CONCEPT_OPTION):
        assert member.value in system


def test_the_prompt_has_nothing_left_to_fill_in(profile: Profile) -> None:
    system = render_system_prompt(mandate=profile.mandate_text(), version=DEFAULT_PROMPT_VERSION)

    assert "{{" not in system
    assert "<!--" not in system


def test_an_unknown_prompt_version_is_named_rather_than_guessed(profile: Profile) -> None:
    with pytest.raises(LookupError, match="assess_v1"):
        render_system_prompt(mandate=profile.mandate_text(), version="assess_v42")
