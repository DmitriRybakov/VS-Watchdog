"""`watchdog screen` end to end: the rules, the model, the score, and the row stored.

The disabled-provider run in the CLI cannot reach three of the six outcomes - a
successful assessment, an assessment that establishes nothing, and one that fails -
and those are exactly the paths that start mattering the day a key exists. They are
driven here by the fake provider, against a real database. No network, no model.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from watchdog.core.enums import Band, DecisionStage, Domain, RunKind, ServiceType
from watchdog.core.models import Tender
from watchdog.core.settings import Settings
from watchdog.llm.disabled import DisabledProvider
from watchdog.llm.fake import FakeProvider
from watchdog.llm.provider import LLMCallError
from watchdog.services import screen as screen_service
from watchdog.storage.repository import Repository

TenderFactory = Callable[..., Tender]

# Each notice carries a marker in its own title, so the fake can answer them
# differently and the test reads as "this notice gets that answer".
GOOD = "hydrogen pre-FEED"
THIN = "rammeavtale"
BROKEN = "havnetjenester"


def _answer(
    *,
    domain: int | None,
    service: int | None,
    stage: int | None,
    quote: str,
) -> str:
    def axis(score: int | None, label: str) -> dict[str, object]:
        return {
            "score": score,
            "label": label if score is not None else "unknown",
            "evidence": [quote] if score is not None else [],
        }

    return json.dumps(
        {
            "domain_fit": axis(domain, Domain.HYDROGEN.value),
            "service_fit": axis(service, ServiceType.EARLY_PHASE_STUDY.value),
            "stage_fit": axis(stage, DecisionStage.PRE_FEED_FEASIBILITY.value),
            "negative_signals": [],
            "missing_information": [] if domain is not None else ["The notice says very little"],
            "short_reason": "Fake answer for a test.",
        }
    )


@pytest.fixture
def stocked(repository: Repository, make_tender: TenderFactory, run_id: str) -> Repository:
    """Three notices, one for each of the three paths a model can take."""
    repository.upsert_tenders(
        [
            make_tender(
                "00000001-2026",
                title_native=f"Konseptstudie for {GOOD}",
                description="Feasibility study for a hydrogen production facility. " * 12,
            ),
            make_tender(
                "00000002-2026",
                title_native=f"{THIN} for tekniske tjenester",
                description=None,
            ),
            make_tender(
                "00000003-2026",
                title_native=f"Drift av {BROKEN}",
                description="Vedlikehold og drift. " * 12,
            ),
        ],
        run_id=run_id,
    )
    return repository


@pytest.fixture
def provider() -> FakeProvider:
    """Answers the three notices by marker: a good one, an empty one, a failure."""
    return FakeProvider(
        model="fake-1",
        responses={
            GOOD: _answer(domain=5, service=5, stage=4, quote="hydrogen production facility"),
            THIN: _answer(domain=None, service=None, stage=None, quote=""),
            # Both attempts fail, which is what "failed twice" means to the policy.
            BROKEN: [LLMCallError("the provider returned 503"), LLMCallError("and again")],
        },
    )


def _screen(
    store: Repository, tmp_path: Path, provider: object, **kwargs: object
) -> screen_service.ScreenOutcome:
    return screen_service.screen_register(
        settings=Settings(data_dir=tmp_path),
        provider=provider,  # type: ignore[arg-type]
        repository=store,
        use_cache=False,
        **kwargs,  # type: ignore[arg-type]
    )


# ------------------------------------------------------------- with a model on


def test_each_of_the_three_model_paths_ends_in_a_stored_result(
    stocked: Repository, provider: FakeProvider, tmp_path: Path
) -> None:
    outcome = _screen(stocked, tmp_path, provider)

    assert outcome.ai_enabled is True
    assert outcome.screened == 3

    results = stocked.latest_screening_for(
        ["ted:00000001-2026", "ted:00000002-2026", "ted:00000003-2026"]
    )
    assert set(results) == {"ted:00000001-2026", "ted:00000002-2026", "ted:00000003-2026"}


def test_a_successful_assessment_is_scored_from_the_weights(
    stocked: Repository, provider: FakeProvider, tmp_path: Path
) -> None:
    _screen(stocked, tmp_path, provider)
    result = stocked.latest_screening_for(["ted:00000001-2026"])["ted:00000001-2026"]

    # 0.50*5 + 0.30*5 + 0.20*4 = 4.8 -> 5.
    assert result.score == 5
    assert result.band is Band.SHORTLIST
    assert result.reason_codes == []
    assert result.rules_only_score is None
    assert result.axes_established == 3
    assert result.assessment is not None
    assert result.ai_enabled is True
    assert "Weighted 4.8 -> 5" in result.explanation


def test_an_assessment_that_establishes_nothing_is_held_for_review(
    stocked: Repository, provider: FakeProvider, tmp_path: Path
) -> None:
    _screen(stocked, tmp_path, provider)
    result = stocked.latest_screening_for(["ted:00000002-2026"])["ted:00000002-2026"]

    assert result.score == 3
    assert result.band is Band.REVIEW
    assert result.reason_codes == ["INSUFFICIENT_INFORMATION"]
    assert result.axes_established == 0
    # It was assessed: the answer is stored even though it established nothing.
    assert result.assessment is not None
    assert "no weighted score was calculated" in result.explanation


def test_a_failed_assessment_is_stored_visible_and_screened_again_next_run(
    stocked: Repository, provider: FakeProvider, tmp_path: Path
) -> None:
    outcome = _screen(stocked, tmp_path, provider)
    result = stocked.latest_screening_for(["ted:00000003-2026"])["ted:00000003-2026"]

    assert outcome.to_retry == 1
    assert result.score == 3
    assert result.band is Band.REVIEW
    assert result.reason_codes == ["ASSESSMENT_FAILED"]
    assert result.assessment is None
    assert "503" in result.explanation

    # The row exists so the notice is visible, and it never counts as screened.
    stale = stocked.ids_needing_screening(outcome.versions, provider.name, provider.model)
    assert stale == ["ted:00000003-2026"]


def test_a_second_run_re_screens_only_what_is_stale(
    stocked: Repository, provider: FakeProvider, tmp_path: Path
) -> None:
    _screen(stocked, tmp_path, provider)
    again = _screen(stocked, tmp_path, provider)

    assert again.screened == 1
    assert again.already_current == 2


def test_a_screening_run_records_what_judged_it(
    stocked: Repository, provider: FakeProvider, tmp_path: Path
) -> None:
    outcome = _screen(stocked, tmp_path, provider)

    run = stocked.list_runs()[0]
    assert run.kind is RunKind.SCREEN
    assert run.run_id == outcome.run_id
    assert run.provider == "fake"
    assert run.model == "fake-1"
    assert run.prompt_version == "assess_v1"
    assert run.counts["screened"] == 3
    assert run.tokens_in > 0


def test_a_new_result_supersedes_the_previous_one_and_never_edits_it(
    stocked: Repository, provider: FakeProvider, tmp_path: Path
) -> None:
    _screen(stocked, tmp_path, provider)
    _screen(stocked, tmp_path, provider, rescreen=True)

    history = stocked.screening_history("ted:00000001-2026")
    assert len(history) == 2
    assert [item.superseded for item in history] == [False, True]
    assert history[0].score == history[1].score


# ------------------------------------------------------------ with no model at all


def test_with_no_model_every_notice_is_graded_and_nothing_is_shortlisted(
    stocked: Repository, tmp_path: Path
) -> None:
    outcome = _screen(stocked, tmp_path, DisabledProvider())

    assert outcome.ai_enabled is False
    assert outcome.screened == 3
    assert outcome.by_band.get(Band.SHORTLIST.value, 0) == 0
    assert outcome.priority_label == "Rules priority"

    for result in stocked.latest_screening_for(
        ["ted:00000001-2026", "ted:00000002-2026", "ted:00000003-2026"]
    ).values():
        assert result.rules_only_score is not None
        assert result.assessment is None
        assert result.ai_enabled is False
        assert result.band is not Band.SHORTLIST


def test_the_report_orders_on_the_rules_grade_not_on_the_score(
    stocked: Repository, tmp_path: Path
) -> None:
    """The notice with real evidence comes first, above one with none scored 2."""
    outcome = _screen(stocked, tmp_path, DisabledProvider())

    top = outcome.top[0]
    assert top.tender_id == "ted:00000001-2026"
    assert top.rules_only_score is not None
    assert top.priority == top.rules_only_score
    assert [row.priority for row in outcome.top] == sorted(
        (row.priority for row in outcome.top), reverse=True
    )


def test_the_rules_grade_is_counted_apart_from_the_score(
    stocked: Repository, tmp_path: Path
) -> None:
    """A notice with no evidence scores 2 and grades 0. Counting them together hides it."""
    outcome = _screen(stocked, tmp_path, DisabledProvider())

    assert sum(outcome.by_score.values()) == 3
    assert sum(outcome.by_rules_only_score.values()) == 3
    assert 0 in outcome.by_rules_only_score
    assert 0 not in outcome.by_score


def test_switching_a_model_on_makes_every_rules_only_result_stale(
    stocked: Repository, provider: FakeProvider, tmp_path: Path
) -> None:
    _screen(stocked, tmp_path, DisabledProvider())
    outcome = _screen(stocked, tmp_path, provider)

    assert outcome.screened == 3
    assert outcome.already_current == 0
