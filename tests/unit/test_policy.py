"""The scoring policy: weights, caps, bands, overrides and the rules-only path.

Nothing here calls a model or touches a database. The policy is deterministic, so
every one of these is an exact statement about what a given set of evidence
produces - which is what makes a score defensible to a colleague.

The shipped config/policy.yaml is used wherever possible rather than a fixture, so
a change to the file that breaks the policy shows up here.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from watchdog.core.enums import (
    Band,
    DecisionStage,
    Domain,
    Polarity,
    RuleSignal,
    RulesRoute,
    RuleStrength,
    ServiceType,
)
from watchdog.core.models import Assessment, RuleMatch, RulesResult, Tender, TextBlock
from watchdog.llm.provider import Usage
from watchdog.screening.assess import AssessmentOutcome
from watchdog.screening.policy import (
    ASSESSMENT_FAILED,
    AXIS_UNKNOWN,
    EXCLUDED_BY_RULE,
    INSUFFICIENT_INFORMATION,
    LOW_CONFIDENCE,
    MISSING_INFORMATION,
    MULTI_LOT,
    RULES_MODEL_DISAGREEMENT,
    RULES_ONLY,
    RULES_ONLY_NO_EVIDENCE,
    ConditionError,
    PolicyConfig,
    RulesEvidence,
    compile_condition,
    decide,
    load_policy,
    parse_policy,
    ranking_key,
    rules_only_grade,
)

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

# Long enough to cost nothing against confidence.description_chars_full.
FULL_DESCRIPTION = "Pre-FEED study including techno-economic analysis of the connection. " * 10


@pytest.fixture(scope="module")
def policy() -> PolicyConfig:
    """The shipped policy as it ships. Tests read the real numbers, not a mock."""
    return load_policy(CONFIG_DIR / "policy.yaml")


def _tender(
    make_tender: Any,
    *,
    description: str = FULL_DESCRIPTION,
    multi_lot: bool = False,
    cpv_all: list[str] | None = None,
    language: str = "eng",
    published: date | None = date(2026, 3, 1),
) -> Tender:
    tender = make_tender(description=description, published_date=published)
    blocks = [
        TextBlock(field="title-proc", language=language, text="Feasibility study, hydrogen"),
        TextBlock(field="description-proc", language=language, text=description),
    ]
    return tender.model_copy(
        update={
            "screening_blocks": blocks,
            "multi_lot": multi_lot,
            "cpv_all": ["71241000"] if cpv_all is None else cpv_all,
        }
    )


def _match(
    rule_id: str,
    signal: RuleSignal,
    strength: RuleStrength = RuleStrength.HIGH,
) -> RuleMatch:
    return RuleMatch(
        rule_id=rule_id,
        signal=signal,
        strength=strength,
        alias_matched="hydrogen",
        field="title-proc",
        evidence="Feasibility study, hydrogen",
        polarity=(Polarity.NEGATIVE if signal is RuleSignal.EXCLUSION else Polarity.POSITIVE),
    )


def _rules(
    *,
    matches: list[RuleMatch] | None = None,
    domains: list[Domain] | None = None,
    cpv_match: bool = False,
    route: RulesRoute = RulesRoute.ASSESS,
) -> RulesResult:
    return RulesResult(
        tender_id="ted:00123456-2026",
        matches=matches or [],
        domains_hit=domains or [],
        cpv_match=cpv_match,
        has_exclusion=any(match.signal is RuleSignal.EXCLUSION for match in (matches or [])),
        route=route,
        rules_version="3",
    )


def _assessment(
    *,
    domain: int | None = 5,
    service: int | None = 5,
    stage: int | None = 4,
    missing: list[str] | None = None,
) -> Assessment:
    def axis(score: int | None, label: Any, unknown: Any) -> dict[str, Any]:
        quotes = ["feasibility study for the hydrogen connection"] if score is not None else []
        return {
            "score": score,
            "label": label if score is not None else unknown,
            "evidence": quotes,
        }

    return Assessment.model_validate(
        {
            "domain_fit": axis(domain, Domain.OFFSHORE_WIND, Domain.UNKNOWN),
            "service_fit": axis(service, ServiceType.EARLY_PHASE_STUDY, ServiceType.UNKNOWN),
            "stage_fit": axis(stage, DecisionStage.PRE_FEED_FEASIBILITY, DecisionStage.UNKNOWN),
            "negative_signals": [],
            "missing_information": missing or [],
            "short_reason": "Feasibility study for a hydrogen connection.",
        }
    )


def _outcome(assessment: Assessment | None, *, error: str | None = None) -> AssessmentOutcome:
    return AssessmentOutcome(
        tender_id="ted:00123456-2026",
        title="Feasibility study, hydrogen",
        assessment=assessment,
        usage=Usage(attempts=1),
        input_hash="0" * 64,
        truncated=False,
        confidence=1.0 if assessment is not None else 0.0,
        confidence_reasons=() if assessment is not None else ("Nothing was established",),
        error=error,
        error_type="LLMCallError" if error else None,
    )


# ------------------------------------------------------------------- weighting


@pytest.mark.parametrize(
    ("domain", "service", "stage", "expected"),
    [
        # 0.50*5 + 0.30*5 + 0.20*5 = 5.0
        (5, 5, 5, 5),
        # 0.50*5 + 0.30*5 + 0.20*4 = 4.8
        (5, 5, 4, 5),
        # 0.50*4 + 0.30*3 + 0.20*3 = 3.5, which rounds up, not to even
        (4, 3, 3, 4),
        # 0.50*5 + 0.30*4 + 0.20*4 = 4.5, likewise
        (5, 4, 4, 5),
        # 0.50*4 + 0.30*3 + 0.20*2 = 3.3
        (4, 3, 2, 3),
        # 0.50*3 + 0.30*3 + 0.20*2 = 2.8
        (3, 3, 2, 3),
        # 0.50*2 + 0.30*2 + 0.20*2 = 2.0
        (2, 2, 2, 2),
        # 0.50*0 + 0.30*5 + 0.20*5 = 2.5, then the out-of-domain cap takes it to 1
        (0, 5, 5, 1),
    ],
)
def test_the_weighted_sum_rounds_half_up(
    make_tender: Any, policy: PolicyConfig, domain: int, service: int, stage: int, expected: int
) -> None:
    decision = decide(
        _tender(make_tender),
        _rules(),
        policy=policy,
        outcome=_outcome(_assessment(domain=domain, service=service, stage=stage)),
        ai_enabled=True,
    )
    assert decision.score == expected


def test_exactly_three_point_five_rounds_to_four(make_tender: Any, policy: PolicyConfig) -> None:
    decision = decide(
        _tender(make_tender),
        _rules(),
        policy=policy,
        outcome=_outcome(_assessment(domain=4, service=3, stage=3)),
        ai_enabled=True,
    )
    assert decision.weighted == pytest.approx(3.5)
    assert decision.score == 4
    assert decision.band is Band.SHORTLIST


def test_exactly_four_point_five_rounds_to_five(make_tender: Any, policy: PolicyConfig) -> None:
    decision = decide(
        _tender(make_tender),
        _rules(),
        policy=policy,
        outcome=_outcome(_assessment(domain=5, service=4, stage=4)),
        ai_enabled=True,
    )
    assert decision.weighted == pytest.approx(4.5)
    assert decision.score == 5


def test_an_unknown_axis_is_left_out_rather_than_counted_as_zero(
    make_tender: Any, policy: PolicyConfig
) -> None:
    """Domain 5 with nothing else established scores 5, not 2.5. It still goes to review."""
    decision = decide(
        _tender(make_tender),
        _rules(),
        policy=policy,
        outcome=_outcome(_assessment(domain=5, service=None, stage=None)),
        ai_enabled=True,
    )
    assert decision.weighted == pytest.approx(5.0)
    assert decision.weighted_score == 5
    assert decision.axes_established == 1
    assert decision.band is Band.REVIEW
    assert AXIS_UNKNOWN in decision.reason_codes
    assert "Weighed on 1 of 3 axes" in decision.explanation


# ----------------------------------------------------------------------- caps


def test_a_high_service_and_stage_cannot_rescue_an_out_of_domain_tender(
    make_tender: Any, policy: PolicyConfig
) -> None:
    decision = decide(
        _tender(make_tender),
        _rules(),
        policy=policy,
        outcome=_outcome(_assessment(domain=1, service=5, stage=5)),
        ai_enabled=True,
    )
    assert decision.weighted_score == 3
    assert decision.score == 1
    assert decision.band is Band.ARCHIVE
    assert "OUT_OF_DOMAIN" in decision.reason_codes
    assert "Capped to 1 (out of domain)" in decision.explanation


def test_an_execution_scope_in_a_core_domain_is_capped_and_tagged(
    make_tender: Any, policy: PolicyConfig
) -> None:
    """An offshore wind construction package: not a bid, still worth knowing about."""
    decision = decide(
        _tender(make_tender),
        _rules(),
        policy=policy,
        outcome=_outcome(_assessment(domain=5, service=1, stage=1)),
        ai_enabled=True,
    )
    assert decision.weighted_score == 3
    assert decision.score == 2
    assert "EXECUTION_DOMINATED" in decision.reason_codes
    assert "PROJECT_INTELLIGENCE" in decision.reason_codes


def test_a_cap_never_raises_a_score(make_tender: Any, policy: PolicyConfig) -> None:
    """Both caps hold, and the lower one does not pull the score back up to 2."""
    decision = decide(
        _tender(make_tender),
        _rules(),
        policy=policy,
        outcome=_outcome(_assessment(domain=0, service=0, stage=0)),
        ai_enabled=True,
    )
    assert decision.score == 1


def test_a_cap_is_not_applied_on_an_axis_that_is_unknown(
    make_tender: Any, policy: PolicyConfig
) -> None:
    """`domain_fit <= 1` is false when domain_fit is not established, never true."""
    decision = decide(
        _tender(make_tender),
        _rules(),
        policy=policy,
        outcome=_outcome(_assessment(domain=None, service=5, stage=5)),
        ai_enabled=True,
    )
    assert "OUT_OF_DOMAIN" not in decision.reason_codes
    assert decision.score == 5


# --------------------------------------------------------------------- bands


@pytest.mark.parametrize(
    ("domain", "service", "stage", "band"),
    [
        (5, 5, 5, Band.SHORTLIST),
        (4, 4, 4, Band.SHORTLIST),
        (3, 3, 3, Band.REVIEW),
        (2, 2, 2, Band.ARCHIVE),
    ],
)
def test_the_bands_follow_the_thresholds(
    make_tender: Any, policy: PolicyConfig, domain: int, service: int, stage: int, band: Band
) -> None:
    decision = decide(
        _tender(make_tender),
        _rules(),
        policy=policy,
        outcome=_outcome(_assessment(domain=domain, service=service, stage=stage)),
        ai_enabled=True,
    )
    assert decision.band is band


# --------------------------------------------------------- review-forcing overrides


def test_an_unknown_axis_forces_review(make_tender: Any, policy: PolicyConfig) -> None:
    decision = decide(
        _tender(make_tender),
        _rules(),
        policy=policy,
        outcome=_outcome(_assessment(domain=5, service=5, stage=None)),
        ai_enabled=True,
    )
    assert decision.score == 5
    assert decision.band is Band.REVIEW
    assert AXIS_UNKNOWN in decision.reason_codes


def test_missing_information_forces_review(make_tender: Any, policy: PolicyConfig) -> None:
    assessment = _assessment(missing=["The notice does not say what stage the project is at"])
    decision = decide(
        _tender(make_tender),
        _rules(),
        policy=policy,
        outcome=_outcome(assessment),
        ai_enabled=True,
    )
    assert decision.band is Band.REVIEW
    assert MISSING_INFORMATION in decision.reason_codes


def test_low_confidence_forces_review(make_tender: Any, policy: PolicyConfig) -> None:
    """Scored 5 on what was established, and still reviewed because little was.

    The other triggers are switched off so that only confidence can be what moved
    the band - a thin, code-less, non-English notice fires several of them at once.
    """
    only_confidence = policy.model_copy(
        update={
            "review_triggers": policy.review_triggers.model_copy(update={"axis_unknown": False})
        }
    )
    tender = _tender(make_tender, description="Hydrogen.", cpv_all=[], language="deu")
    decision = decide(
        tender,
        _rules(),
        policy=only_confidence,
        outcome=_outcome(_assessment(stage=None)),
        ai_enabled=True,
    )
    assert decision.score == 5
    assert decision.confidence < policy.confidence.review_below
    assert decision.band is Band.REVIEW
    assert LOW_CONFIDENCE in decision.reason_codes


def test_rules_and_model_disagreeing_on_domain_forces_review(
    make_tender: Any, policy: PolicyConfig
) -> None:
    rules = _rules(
        matches=[_match("domain_hydrogen", RuleSignal.DOMAIN)], domains=[Domain.HYDROGEN]
    )
    decision = decide(
        _tender(make_tender),
        rules,
        policy=policy,
        outcome=_outcome(_assessment(domain=1, service=5, stage=5)),
        ai_enabled=True,
    )
    assert decision.band is Band.REVIEW
    assert RULES_MODEL_DISAGREEMENT in decision.reason_codes
    assert any("buyer's own words" in reason for reason in decision.confidence_reasons)


def test_the_rules_finding_nothing_is_not_a_disagreement(
    make_tender: Any, policy: PolicyConfig
) -> None:
    """Our vocabulary is English and TED titles are not. Silence is never a conflict."""
    decision = decide(
        _tender(make_tender),
        _rules(),
        policy=policy,
        outcome=_outcome(_assessment()),
        ai_enabled=True,
    )
    assert RULES_MODEL_DISAGREEMENT not in decision.reason_codes


def test_a_multi_lot_notice_forces_review(make_tender: Any, policy: PolicyConfig) -> None:
    """Not because the scope may be split - because we assess the lots as one text.

    "Hydrogen" from an equipment lot and "feasibility study" from an unrelated one
    combine into a 5/5 for a scope that does not exist. Until the assessment can
    show that all three axes came from the same lot, a person has to look.
    """
    decision = decide(
        _tender(make_tender, multi_lot=True),
        _rules(),
        policy=policy,
        outcome=_outcome(_assessment()),
        ai_enabled=True,
    )
    assert decision.score == 5
    assert decision.band is Band.REVIEW
    assert MULTI_LOT in decision.reason_codes


def test_all_three_axes_unknown_produces_no_weighted_score_at_all(
    make_tender: Any, policy: PolicyConfig
) -> None:
    """The weights are not used here. A number derived from nothing reads as a judgement."""
    decision = decide(
        _tender(make_tender),
        _rules(),
        policy=policy,
        outcome=_outcome(_assessment(domain=None, service=None, stage=None)),
        ai_enabled=True,
    )
    assert decision.weighted is None
    assert decision.weighted_score is None
    assert decision.axes_established == 0
    assert decision.score == policy.fallbacks.insufficient_information
    assert decision.band is Band.REVIEW
    assert decision.reason_codes == [INSUFFICIENT_INFORMATION]
    assert "no weighted score was calculated" in decision.explanation


def test_the_insufficient_information_score_comes_from_the_policy_file(
    make_tender: Any, policy: PolicyConfig
) -> None:
    edited = policy.model_copy(
        update={"fallbacks": policy.fallbacks.model_copy(update={"insufficient_information": 4})}
    )
    decision = decide(
        _tender(make_tender),
        _rules(),
        policy=edited,
        outcome=_outcome(_assessment(domain=None, service=None, stage=None)),
        ai_enabled=True,
    )
    assert decision.score == 4
    assert decision.band is Band.REVIEW


# ------------------------------------------------------------ the other outcomes


def test_an_archive_candidate_is_scored_one_and_archived_not_deleted(
    make_tender: Any, policy: PolicyConfig
) -> None:
    rules = _rules(
        matches=[_match("exclude_health", RuleSignal.EXCLUSION)],
        route=RulesRoute.ARCHIVE_CANDIDATE,
    )
    decision = decide(_tender(make_tender), rules, policy=policy, ai_enabled=True)

    assert decision.score == 1
    assert decision.band is Band.ARCHIVE
    assert decision.reason_codes == [EXCLUDED_BY_RULE]
    assert decision.rules_only_score is None
    assert "stays searchable" in decision.explanation


def test_a_failed_assessment_is_held_for_review_and_retried(
    make_tender: Any, policy: PolicyConfig
) -> None:
    decision = decide(
        _tender(make_tender),
        _rules(matches=[_match("domain_hydrogen", RuleSignal.DOMAIN)], domains=[Domain.HYDROGEN]),
        policy=policy,
        outcome=_outcome(None, error="the provider returned 503"),
        ai_enabled=True,
    )
    assert decision.score == 3
    assert decision.band is Band.REVIEW
    assert decision.reason_codes == [ASSESSMENT_FAILED]
    assert decision.retry_next_run is True
    assert "503" in decision.explanation


# ------------------------------------------------------------- the rules-only path


@pytest.mark.parametrize(
    ("matches", "cpv_match", "expected"),
    [
        # A high-strength domain rule and an activity rule: the strongest claim the
        # rules can make on their own.
        (
            [
                ("domain_hydrogen", RuleSignal.DOMAIN, RuleStrength.HIGH),
                ("activity_study", RuleSignal.ACTIVITY, RuleStrength.HIGH),
            ],
            False,
            3,
        ),
        # A high-strength domain rule with no activity rule.
        ([("domain_hydrogen", RuleSignal.DOMAIN, RuleStrength.HIGH)], False, 2),
        # A medium domain rule on its own.
        ([("domain_grid", RuleSignal.DOMAIN, RuleStrength.MEDIUM)], False, 2),
        # A CPV guard code together with an activity rule.
        ([("activity_study", RuleSignal.ACTIVITY, RuleStrength.HIGH)], True, 2),
        # Activity evidence only.
        ([("activity_study", RuleSignal.ACTIVITY, RuleStrength.HIGH)], False, 1),
        # Supporting terms only - they never establish a domain.
        ([("domain_energy", RuleSignal.DOMAIN, RuleStrength.SUPPORTING)], False, 1),
        # A CPV code on its own is a recall filter, not evidence of relevance.
        ([], True, 0),
        # Nothing at all.
        ([], False, 0),
    ],
)
def test_the_rules_only_score_is_graded_by_the_evidence(
    policy: PolicyConfig,
    matches: list[tuple[str, RuleSignal, RuleStrength]],
    cpv_match: bool,
    expected: int,
) -> None:
    rules = _rules(
        matches=[_match(rule_id, signal, strength) for rule_id, signal, strength in matches],
        cpv_match=cpv_match,
    )
    assert rules_only_grade(RulesEvidence.of(rules), policy) == expected


def test_rules_only_never_shortlists_however_strong_the_evidence(
    make_tender: Any, policy: PolicyConfig
) -> None:
    rules = _rules(
        matches=[
            _match("domain_hydrogen", RuleSignal.DOMAIN, RuleStrength.HIGH),
            _match("activity_study", RuleSignal.ACTIVITY, RuleStrength.HIGH),
        ],
        domains=[Domain.HYDROGEN],
        cpv_match=True,
    )
    decision = decide(_tender(make_tender), rules, policy=policy, ai_enabled=False)

    assert decision.score == 3
    assert decision.rules_only_score == 3
    assert decision.band is Band.REVIEW
    assert decision.reason_codes == [RULES_ONLY]
    assert decision.ai_enabled is False
    assert decision.weighted is None


def test_rules_only_with_no_evidence_is_archived_and_flagged(
    make_tender: Any, policy: PolicyConfig
) -> None:
    decision = decide(_tender(make_tender), _rules(), policy=policy, ai_enabled=False)

    assert decision.score == 2
    assert decision.rules_only_score == 0
    assert decision.band is Band.ARCHIVE
    assert decision.reason_codes == [RULES_ONLY_NO_EVIDENCE]


def test_the_rules_only_register_orders_strong_evidence_first(
    make_tender: Any, policy: PolicyConfig
) -> None:
    """Forty notices with real evidence at the top, boilerplate at the bottom."""
    strong = _rules(
        matches=[
            _match("domain_hydrogen", RuleSignal.DOMAIN, RuleStrength.HIGH),
            _match("domain_ccs", RuleSignal.DOMAIN, RuleStrength.HIGH),
            _match("activity_study", RuleSignal.ACTIVITY, RuleStrength.HIGH),
        ]
    )
    one_domain = _rules(
        matches=[
            _match("domain_hydrogen", RuleSignal.DOMAIN, RuleStrength.HIGH),
            _match("activity_study", RuleSignal.ACTIVITY, RuleStrength.HIGH),
        ]
    )
    medium = _rules(matches=[_match("domain_grid", RuleSignal.DOMAIN, RuleStrength.MEDIUM)])
    weak = _rules(matches=[_match("activity_study", RuleSignal.ACTIVITY, RuleStrength.HIGH)])

    tender = _tender(make_tender)
    ordered = sorted(
        (
            (
                name,
                ranking_key(
                    decide(tender, rules, policy=policy, ai_enabled=False),
                    tender.published_date,
                ),
            )
            for name, rules in (
                ("weak", weak),
                ("medium", medium),
                ("one_domain", one_domain),
                ("strong", strong),
            )
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    assert [name for name, _ in ordered] == ["strong", "one_domain", "medium", "weak"]


def test_a_rules_only_score_is_kept_apart_from_an_assessed_one(
    make_tender: Any, policy: PolicyConfig
) -> None:
    """Both are 3. Only one of them is a claim that anything judged the notice."""
    assessed = decide(
        _tender(make_tender),
        _rules(),
        policy=policy,
        outcome=_outcome(_assessment(domain=3, service=3, stage=3)),
        ai_enabled=True,
    )
    rules_only = decide(
        _tender(make_tender),
        _rules(
            matches=[
                _match("domain_hydrogen", RuleSignal.DOMAIN, RuleStrength.HIGH),
                _match("activity_study", RuleSignal.ACTIVITY, RuleStrength.HIGH),
            ]
        ),
        policy=policy,
        ai_enabled=False,
    )

    assert assessed.score == rules_only.score == 3
    assert assessed.rules_only_score is None
    assert rules_only.rules_only_score == 3


# ---------------------------------------------------------------- confidence


def test_confidence_is_full_for_a_complete_notice(make_tender: Any, policy: PolicyConfig) -> None:
    decision = decide(
        _tender(make_tender),
        _rules(),
        policy=policy,
        outcome=_outcome(_assessment()),
        ai_enabled=True,
    )
    assert decision.confidence == 1.0
    assert decision.confidence_reasons == [
        "Every axis was established, and the notice carries a full description and a CPV code"
    ]


def test_confidence_records_each_reason_in_plain_language(
    make_tender: Any, policy: PolicyConfig
) -> None:
    tender = _tender(make_tender, description="Hydrogen.", cpv_all=[], language="fra")
    decision = decide(
        tender,
        _rules(),
        policy=policy,
        outcome=_outcome(_assessment(stage=None)),
        ai_enabled=True,
    )
    joined = " ".join(decision.confidence_reasons)
    assert "characters" in joined
    assert "no CPV code" in joined
    assert "Stage fit was not established" in joined
    assert "no English text" in joined
    assert 0.0 < decision.confidence < 1.0


def test_the_policy_never_widens_the_assessment_stages_confidence(
    make_tender: Any, policy: PolicyConfig
) -> None:
    outcome = _outcome(_assessment())
    narrowed = replace(
        outcome, confidence=0.3, confidence_reasons=("Part of the notice was not read",)
    )
    decision = decide(
        _tender(make_tender), _rules(), policy=policy, outcome=narrowed, ai_enabled=True
    )
    assert decision.confidence == 0.3
    assert "Part of the notice was not read" in decision.confidence_reasons


# ------------------------------------------------------------- the explanation


def test_the_explanation_is_assembled_from_the_parts(
    make_tender: Any, policy: PolicyConfig
) -> None:
    decision = decide(
        _tender(make_tender),
        _rules(),
        policy=policy,
        outcome=_outcome(_assessment(domain=5, service=5, stage=4)),
        ai_enabled=True,
    )
    assert decision.explanation == (
        "Domain: offshore wind 5/5. Service: early phase study 5/5. "
        "Stage: pre feed feasibility 4/5. Weighted 4.8 -> 5. Confidence 1. "
        "Evidence: 'feasibility study for the hydrogen connection'."
    )


def test_the_rules_only_explanation_says_that_nothing_judged_the_notice(
    make_tender: Any, policy: PolicyConfig
) -> None:
    rules = _rules(
        matches=[
            _match("domain_hydrogen", RuleSignal.DOMAIN, RuleStrength.HIGH),
            _match("activity_study", RuleSignal.ACTIVITY, RuleStrength.HIGH),
        ]
    )
    explanation = decide(_tender(make_tender), rules, policy=policy, ai_enabled=False).explanation

    assert "no model is configured" in explanation
    assert "domain_hydrogen" in explanation
    assert "Rules-only score 3 of 3" in explanation
    assert "Feasibility study, hydrogen" in explanation


# ------------------------------------------------------ configuration is data


def test_editing_the_policy_file_changes_the_outcome_with_no_code_change(
    make_tender: Any, tmp_path: Path
) -> None:
    """The whole point of config/policy.yaml. Same evidence, different file, different score."""
    data: dict[str, Any] = yaml.safe_load((CONFIG_DIR / "policy.yaml").read_text(encoding="utf-8"))
    shipped = parse_policy(data)

    data["weights"] = {"domain": 0.20, "service": 0.30, "stage": 0.50}
    data["bands"] = {"shortlist_min": 5, "review_min": 2}
    edited = parse_policy(data)

    tender = _tender(make_tender)
    rules = _rules()
    outcome = _outcome(_assessment(domain=5, service=3, stage=1))

    before = decide(tender, rules, policy=shipped, outcome=outcome, ai_enabled=True)
    after = decide(tender, rules, policy=edited, outcome=outcome, ai_enabled=True)

    # 0.50*5 + 0.30*3 + 0.20*1 = 3.6 -> 4, against
    # 0.20*5 + 0.30*3 + 0.50*1 = 2.4 -> 2.
    assert before.score == 4
    assert before.band is Band.SHORTLIST
    assert after.score == 2
    assert after.band is Band.REVIEW


def test_turning_a_review_trigger_off_changes_the_band(
    make_tender: Any, policy: PolicyConfig
) -> None:
    """The mechanism, shown on a trigger that is safe to switch off.

    Not multi_lot: that one guards against a judgement about a scope that does not
    exist, and config/policy.yaml says why it stays on.
    """
    relaxed = policy.model_copy(
        update={
            "review_triggers": policy.review_triggers.model_copy(
                update={"missing_information": False}
            )
        }
    )
    assessment = _assessment(missing=["The notice does not say what stage the project is at"])
    decision = decide(
        _tender(make_tender),
        _rules(),
        policy=relaxed,
        outcome=_outcome(assessment),
        ai_enabled=True,
    )
    assert decision.band is Band.SHORTLIST
    assert MISSING_INFORMATION not in decision.reason_codes


def test_the_shipped_policy_matches_the_version_recorded_on_a_result(
    policy: PolicyConfig,
) -> None:
    assert policy.policy_version == str(policy.version)


# ------------------------------------------------------------- the when grammar


@pytest.mark.parametrize(
    ("expression", "axes", "expected"),
    [
        ("domain_fit <= 1", {"domain_fit": 1}, True),
        ("domain_fit <= 1", {"domain_fit": 2}, False),
        ("domain_fit <= 1", {"domain_fit": None}, False),
        ("domain_fit >= 3 and service_fit <= 1", {"domain_fit": 5, "service_fit": 1}, True),
        ("domain_fit >= 3 and service_fit <= 1", {"domain_fit": 5, "service_fit": 3}, False),
        ("domain_fit >= 3 and service_fit <= 1", {"domain_fit": None, "service_fit": 1}, False),
        ("stage_fit == 5", {"stage_fit": 5}, True),
        ("stage_fit != 5", {"stage_fit": 5}, False),
    ],
)
def test_a_when_expression_reads_the_axes(
    expression: str, axes: dict[str, int | None], expected: bool
) -> None:
    assert compile_condition(expression).holds(axes) is expected


@pytest.mark.parametrize(
    "expression",
    [
        "",
        "domain_fit",
        "domain_fit <= five",
        "budget >= 3",
        "domain_fit <= 9",
        "domain_fit <= 1 or service_fit <= 1",
        "__import__('os').system('echo')",
    ],
)
def test_an_unreadable_when_expression_is_refused_when_the_file_loads(expression: str) -> None:
    with pytest.raises(ConditionError):
        compile_condition(expression)


def test_weights_that_do_not_add_up_are_refused() -> None:
    with pytest.raises(ValidationError, match="add up to"):
        parse_policy(
            {
                "weights": {"domain": 0.5, "service": 0.3, "stage": 0.1},
                "bands": {"shortlist_min": 4, "review_min": 3},
            }
        )


def test_bands_in_the_wrong_order_are_refused() -> None:
    with pytest.raises(ValidationError, match="must be below"):
        parse_policy(
            {
                "weights": {"domain": 0.5, "service": 0.3, "stage": 0.2},
                "bands": {"shortlist_min": 3, "review_min": 4},
            }
        )


def test_a_cap_name_that_cannot_be_a_reason_code_is_refused() -> None:
    with pytest.raises(ValidationError, match="reason code"):
        parse_policy(
            {
                "weights": {"domain": 0.5, "service": 0.3, "stage": 0.2},
                "bands": {"shortlist_min": 4, "review_min": 3},
                "caps": {"Out Of Domain": {"when": "domain_fit <= 1", "max_score": 1}},
            }
        )
