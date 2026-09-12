"""The gateway: what happens when the model answers badly, or not at all."""

from __future__ import annotations

import pytest

from watchdog.core.enums import DecisionStage, Domain, ServiceType
from watchdog.core.models import Assessment, AxisScore
from watchdog.core.settings import Settings
from watchdog.llm.disabled import DisabledProvider
from watchdog.llm.fake import FakeProvider, demo_assessment
from watchdog.llm.provider import (
    LLMOutputError,
    ProviderDisabled,
    ProviderNotConfigured,
    Usage,
    get_provider,
    input_hash,
)

GOOD_ANSWER = Assessment(
    domain_fit=AxisScore[Domain](score=5, label=Domain.HYDROGEN, evidence=["grønt hydrogen"]),
    service_fit=AxisScore[ServiceType](
        score=5, label=ServiceType.EARLY_PHASE_STUDY, evidence=["mulighetsstudie"]
    ),
    stage_fit=AxisScore[DecisionStage](
        score=5, label=DecisionStage.CONCEPT_OPTION, evidence=["Konseptstudie"]
    ),
    short_reason="Concept study for green hydrogen production.",
).model_dump_json()

# Valid JSON, wrong shape: a label that is not in the vocabulary.
WRONG_SHAPE = '{"domain_fit": {"score": 9, "label": "space_lasers", "evidence": []}}'

NOT_JSON = "Sure! Here is the assessment you asked for."


def test_the_default_provider_is_disabled_and_says_so_in_its_own_type() -> None:
    provider = DisabledProvider()

    assert provider.enabled is False
    with pytest.raises(ProviderDisabled):
        provider.assess(system="s", user="u", schema=Assessment)


def test_get_provider_returns_the_disabled_one_when_nothing_is_configured() -> None:
    provider = get_provider(Settings(llm_provider="disabled"))

    assert provider.name == "disabled"
    assert provider.enabled is False


def test_get_provider_refuses_a_name_it_does_not_know() -> None:
    with pytest.raises(ProviderNotConfigured, match="no model provider"):
        get_provider(Settings(llm_provider="oracle"))


def test_a_valid_answer_is_returned_after_one_call() -> None:
    provider = FakeProvider(responses={"hydrogen": GOOD_ANSWER})

    assessment, usage = provider.assess(system="s", user="a hydrogen notice", schema=Assessment)

    assert assessment.domain_fit.label is Domain.HYDROGEN
    assert usage.attempts == 1
    assert usage.repaired is False
    assert provider.call_count == 1


@pytest.mark.parametrize("bad_answer", [WRONG_SHAPE, NOT_JSON], ids=["wrong-shape", "not-json"])
def test_a_schema_failure_is_repaired_with_exactly_one_retry(bad_answer: str) -> None:
    provider = FakeProvider(responses={"hydrogen": [bad_answer, GOOD_ANSWER]})

    assessment, usage = provider.assess(system="s", user="a hydrogen notice", schema=Assessment)

    assert assessment.domain_fit.label is Domain.HYDROGEN
    assert provider.call_count == 2
    assert usage.attempts == 2
    assert usage.repaired is True


def test_the_repair_attempt_carries_the_validation_error_and_not_the_rejected_answer() -> None:
    provider = FakeProvider(responses={"hydrogen": [WRONG_SHAPE, GOOD_ANSWER]})

    provider.assess(system="s", user="a hydrogen notice", schema=Assessment)

    repair = provider.calls[1].user
    assert "Your previous answer was rejected" in repair
    assert "domain_fit.label" in repair
    assert "space_lasers" not in repair


def test_two_bad_answers_stop_rather_than_loop() -> None:
    provider = FakeProvider(responses={"hydrogen": [NOT_JSON, WRONG_SHAPE]})

    with pytest.raises(LLMOutputError) as raised:
        provider.assess(system="s", user="a hydrogen notice", schema=Assessment)

    assert provider.call_count == 2
    assert raised.value.attempts == 2


def test_usage_from_a_cached_answer_is_never_billed_twice() -> None:
    usage = Usage(tokens_in=100, tokens_out=50, cached=True)

    assert usage.billable_tokens_in == 0
    assert usage.billable_tokens_out == 0


def test_the_input_hash_cannot_be_fooled_by_moving_the_boundary() -> None:
    assert input_hash("ab", "c") != input_hash("a", "bc")


def test_the_demo_answer_quotes_the_notice_it_was_given() -> None:
    assessment = demo_assessment("Feasibility study for a hydrogen production facility")

    assert assessment.domain_fit.label is Domain.HYDROGEN
    assert "hydrogen" in assessment.domain_fit.evidence[0]
