"""Stage 3: one number, one band, and the reasons behind both.

This module is **entirely deterministic**. It calls no model and reads no file:
the numbers come from ``config/policy.yaml``, parsed into :class:`PolicyConfig`
by the service layer and passed in. Given the same evidence and the same policy
it gives the same answer, every time, and the answer can be recomputed from what
is stored.

What it does, in order:

1. Combines the three axis scores into a weighted number on the 0-5 scale and
   rounds it half up into the 1-5 score the register shows.
2. Applies the caps, which can only ever lower a score.
3. Bands the score, then lets a review trigger force REVIEW regardless.

Three things this module will not do:

- **Treat an unknown axis as a zero.** An axis the assessment could not establish
  is left out of the weighted sum and the remaining weights are re-shared. Not
  knowing something is never evidence against a notice; it forces a review.
- **Generate an explanation.** The sentence a colleague reads is assembled from
  the parts that produced the score - the labels, the numbers, the quotes - so
  every word of it can be checked against something stored.
- **Merge a rules-only score with an assessed one.** They are different kinds of
  claim. ``rules_only_score`` has its own field, so "score 3" never has to be
  interpreted before it can be read.

Every path through this module has a defined outcome. There are five, and they
are listed in :func:`decide`.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from watchdog.core.enums import Band, RuleSignal, RulesRoute, RuleStrength
from watchdog.core.models import (
    ASSESSMENT_FAILED,
    RANKING_FIELDS,
    Assessment,
    RulesResult,
    Tender,
    ranking_key,
)
from watchdog.screening.assess import AssessmentOutcome

DEFAULT_POLICY_PATH = Path("config/policy.yaml")

# The three axes, in the order they are read and shown. The names are the ones the
# assessment schema and the `when:` expressions both use.
AXES = ("domain_fit", "service_fit", "stage_fit")

# How each axis is named on screen.
_AXIS_LABELS = {"domain_fit": "Domain", "service_fit": "Service", "stage_fit": "Stage"}

# Reason codes with a fixed meaning. The rest are the keys of the `caps:` and
# `reason_codes:` blocks of config/policy.yaml, upper-cased - those are data, so
# adding one is a configuration change and not a release.
#
# ASSESSMENT_FAILED is defined in core.models: the register reads it to tell a
# failed assessment apart from a rules grade, and core cannot import this package.
EXCLUDED_BY_RULE = "EXCLUDED_BY_RULE"
RULES_ONLY = "RULES_ONLY"
RULES_ONLY_NO_EVIDENCE = "RULES_ONLY_NO_EVIDENCE"
INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"
AXIS_UNKNOWN = "AXIS_UNKNOWN"
MISSING_INFORMATION = "MISSING_INFORMATION"
LOW_CONFIDENCE = "LOW_CONFIDENCE"
RULES_MODEL_DISAGREEMENT = "RULES_MODEL_DISAGREEMENT"
MULTI_LOT = "MULTI_LOT"

# How much evidence one strength is worth against another, for "the strongest
# domain rule that matched". Not in core.enums: it is an ordering this module
# imposes, not a fact about the vocabulary.
_STRENGTH_RANK = {RuleStrength.SUPPORTING: 1, RuleStrength.MEDIUM: 2, RuleStrength.HIGH: 3}

# Which language codes count as English. Blocks carry the source's own code,
# lower-cased; TED writes three letters.
_ENGLISH = frozenset({"en", "eng"})

# How many evidence quotes an explanation carries. Enough to show the judgement
# rests on the buyer's words, short enough to read in a register row.
_QUOTES_IN_EXPLANATION = 2


# --------------------------------------------------------------- `when:` grammar


class ConditionError(ValueError):
    """A `when:` expression that is not in the grammar. Raised when policy.yaml loads."""


_OPERATORS: dict[str, Callable[[int, int], bool]] = {
    "<=": operator.le,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    ">": operator.gt,
}

_TERM = re.compile(r"^\s*(?P<axis>[a-z_]+)\s*(?P<op><=|>=|==|!=|<|>)\s*(?P<value>\d+)\s*$")


@dataclass(frozen=True)
class _Term:
    axis: str
    op: str
    value: int

    def holds(self, axes: Mapping[str, int | None]) -> bool:
        """An unknown axis makes this false. Never true, and never a zero."""
        score = axes.get(self.axis)
        if score is None:
            return False
        return _OPERATORS[self.op](score, self.value)


@dataclass(frozen=True)
class Condition:
    """One `when:` expression, parsed once and then only ever read.

    Deliberately not Python. A configuration file that a colleague can edit from
    the settings page must not be able to run code, and a grammar this small can
    be checked when the file loads rather than failing on notice 1,700 of a run.
    """

    text: str
    terms: tuple[_Term, ...]

    def holds(self, axes: Mapping[str, int | None]) -> bool:
        return all(term.holds(axes) for term in self.terms)


@lru_cache(maxsize=128)
def compile_condition(text: str) -> Condition:
    """Parse a `when:` expression, or say exactly what is wrong with it."""
    written = text.strip()
    if not written:
        raise ConditionError("a `when:` expression cannot be empty")

    terms: list[_Term] = []
    for part in re.split(r"\s+and\s+", written):
        found = _TERM.match(part)
        if found is None:
            raise ConditionError(
                f"{part.strip()!r} in {written!r} is not a condition Watchdog can read; "
                "write `<axis> <operator> <number>`, joining several with `and`. "
                f"The operators are {', '.join(_OPERATORS)}"
            )
        axis = found["axis"]
        if axis not in AXES:
            raise ConditionError(
                f"{axis!r} in {written!r} is not a scoring axis; the axes are {', '.join(AXES)}"
            )
        value = int(found["value"])
        if not 0 <= value <= 5:
            raise ConditionError(f"{value} in {written!r} is outside the axis scale of 0 to 5")
        terms.append(_Term(axis=axis, op=found["op"], value=value))

    return Condition(text=written, terms=tuple(terms))


# ------------------------------------------------------------------ the policy


class Weights(BaseModel):
    """How much each axis contributes. Must sum to 1, or the result is not on 0-5."""

    model_config = ConfigDict(frozen=True)

    domain: float = Field(ge=0.0, le=1.0)
    service: float = Field(ge=0.0, le=1.0)
    stage: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_total(self) -> Weights:
        total = self.domain + self.service + self.stage
        if abs(total - 1.0) > 1e-9:
            raise ValueError(
                f"the axis weights add up to {total}, not 1; a weighted score is only on the "
                "0-5 axis scale when they do"
            )
        return self

    def of(self, axis: str) -> Decimal:
        """The weight for an axis, exact - a score is not a floating-point question."""
        value = {"domain_fit": self.domain, "service_fit": self.service, "stage_fit": self.stage}[
            axis
        ]
        return Decimal(str(value))


class Cap(BaseModel):
    """A ceiling that applies when its condition holds. It can only lower a score."""

    model_config = ConfigDict(frozen=True)

    when: str
    max_score: int = Field(ge=1, le=5)
    note: str | None = None

    @field_validator("when")
    @classmethod
    def _check_when(cls, value: str) -> str:
        compile_condition(value)
        return value

    def holds(self, axes: Mapping[str, int | None]) -> bool:
        return compile_condition(self.when).holds(axes)


class Tag(BaseModel):
    """A reason code applied when its condition holds. It never changes a score."""

    model_config = ConfigDict(frozen=True)

    when: str
    note: str | None = None

    @field_validator("when")
    @classmethod
    def _check_when(cls, value: str) -> str:
        compile_condition(value)
        return value

    def holds(self, axes: Mapping[str, int | None]) -> bool:
        return compile_condition(self.when).holds(axes)


class Bands(BaseModel):
    """Where the register puts a score."""

    model_config = ConfigDict(frozen=True)

    shortlist_min: int = Field(ge=1, le=5)
    review_min: int = Field(ge=1, le=5)

    @model_validator(mode="after")
    def _check_order(self) -> Bands:
        if self.review_min >= self.shortlist_min:
            raise ValueError(
                f"review_min ({self.review_min}) must be below shortlist_min "
                f"({self.shortlist_min}), or no score can ever be a review"
            )
        return self

    def band_for(self, score: int) -> Band:
        if score >= self.shortlist_min:
            return Band.SHORTLIST
        if score >= self.review_min:
            return Band.REVIEW
        return Band.ARCHIVE


class ReviewTriggers(BaseModel):
    """Which facts force a person to look, whatever the score says."""

    model_config = ConfigDict(frozen=True)

    axis_unknown: bool = True
    missing_information: bool = True
    low_confidence: bool = True
    rules_model_disagreement: bool = True
    multi_lot: bool = True


class ConfidencePenalties(BaseModel):
    """How much each observable shortcoming costs. Subtracted from 1.0."""

    model_config = ConfigDict(frozen=True)

    short_description: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_cpv: float = Field(default=0.0, ge=0.0, le=1.0)
    axis_unknown: float = Field(default=0.0, ge=0.0, le=1.0)
    rules_model_disagreement: float = Field(default=0.0, ge=0.0, le=1.0)
    no_english_text: float = Field(default=0.0, ge=0.0, le=1.0)


class ConfidenceSettings(BaseModel):
    """What lowers confidence, by how much, and what a low one then does."""

    model_config = ConfigDict(frozen=True)

    description_chars_full: int = Field(default=400, gt=0)
    require_cpv: bool = True
    rules_model_agreement: bool = True
    review_below: float = Field(default=0.5, ge=0.0, le=1.0)
    disagreement_domain_max: int = Field(default=1, ge=0, le=5)
    penalties: ConfidencePenalties = Field(default_factory=ConfidencePenalties)


class RulesOnlySettings(BaseModel):
    """The graded score when no model is configured.

    Three numbers rather than one, because one would put every notice in the
    register on the same row and there would be nothing to sort on.
    """

    model_config = ConfigDict(frozen=True)

    max_score: int = Field(default=3, ge=1, le=5)
    strong_domain_and_activity: int = Field(default=3, ge=1, le=5)
    partial_evidence: int = Field(default=2, ge=1, le=5)
    weak_evidence: int = Field(default=1, ge=1, le=5)
    no_evidence: int = Field(default=2, ge=1, le=5)


class Fallbacks(BaseModel):
    """The scores for the three paths where nothing was weighted."""

    model_config = ConfigDict(frozen=True)

    excluded_by_rule: int = Field(default=1, ge=1, le=5)
    assessment_failed: int = Field(default=3, ge=1, le=5)
    insufficient_information: int = Field(default=3, ge=1, le=5)


class PolicyConfig(BaseModel):
    """Everything the scoring stage is driven by, in one validated object."""

    model_config = ConfigDict(frozen=True)

    version: int = Field(default=1, ge=1)
    weights: Weights
    caps: dict[str, Cap] = Field(default_factory=dict)
    reason_codes: dict[str, Tag] = Field(default_factory=dict)
    bands: Bands
    review_triggers: ReviewTriggers = Field(default_factory=ReviewTriggers)
    confidence: ConfidenceSettings = Field(default_factory=ConfidenceSettings)
    rules_only: RulesOnlySettings = Field(default_factory=RulesOnlySettings)
    fallbacks: Fallbacks = Field(default_factory=Fallbacks)

    @field_validator("caps", "reason_codes")
    @classmethod
    def _check_names(cls, value: dict[str, Any]) -> dict[str, Any]:
        """A name becomes a reason code, so it has to read as one in a filter."""
        for name in value:
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
                raise ValueError(
                    f"{name!r} cannot be a reason code; use lower case letters, digits and "
                    "underscores, starting with a letter"
                )
        return value

    @property
    def policy_version(self) -> str:
        """The version as it is recorded on a screening result."""
        return str(self.version)


def parse_policy(data: dict[str, Any]) -> PolicyConfig:
    """Validate an already-loaded mapping. Pure, so it is easy to test."""
    return PolicyConfig.model_validate(data or {})


def load_policy(path: Path | str = DEFAULT_POLICY_PATH) -> PolicyConfig:
    """Read the seed file. Callers are the service layer only."""
    text = Path(path).read_text(encoding="utf-8")
    return parse_policy(yaml.safe_load(text) or {})


# ----------------------------------------------------------------- the evidence


@dataclass(frozen=True)
class RulesEvidence:
    """What the deterministic stage established, in the terms the policy needs.

    Read off the stored matches rather than the rule set, so a result screened
    under an earlier vocabulary still explains and sorts itself.
    """

    # Rule ids that establish a domain, deduplicated. Supporting terms are not here.
    domain_rules: tuple[str, ...]
    # The strongest domain rule that matched at all, supporting ones included.
    strongest_domain: RuleStrength | None
    supporting_only: bool
    has_activity: bool
    cpv_match: bool
    exclusion_rules: tuple[str, ...]

    @classmethod
    def of(cls, rules: RulesResult) -> RulesEvidence:
        domain = [match for match in rules.matches if match.signal is RuleSignal.DOMAIN]
        establishing = [match for match in domain if match.strength is not RuleStrength.SUPPORTING]
        strongest = max(
            (match.strength for match in domain),
            key=lambda item: _STRENGTH_RANK[item],
            default=None,
        )
        return cls(
            domain_rules=tuple(dict.fromkeys(match.rule_id for match in establishing)),
            strongest_domain=strongest,
            supporting_only=bool(domain) and not establishing,
            has_activity=any(match.signal is RuleSignal.ACTIVITY for match in rules.matches),
            cpv_match=rules.cpv_match,
            exclusion_rules=tuple(
                dict.fromkeys(
                    match.rule_id for match in rules.matches if match.signal is RuleSignal.EXCLUSION
                )
            ),
        )

    @property
    def establishes_domain(self) -> bool:
        return bool(self.domain_rules)

    @property
    def strength_rank(self) -> int:
        """0 when no domain rule matched, up to 3 for a high-strength one."""
        return _STRENGTH_RANK[self.strongest_domain] if self.strongest_domain else 0


# ----------------------------------------------------------------- the decision


class Decision(BaseModel):
    """What the policy concluded, with every intermediate value it used.

    The service layer turns this into a ``ScreeningResult`` by adding the facts
    only it knows: the provider, the model and the version stamps.
    """

    model_config = ConfigDict(frozen=True)

    tender_id: str
    score: int = Field(ge=1, le=5)
    band: Band
    # The graded rules-only claim, kept apart from ``score`` on purpose: a 3 that a
    # model reached and a 3 that four keywords reached are not the same statement.
    # None whenever a model assessed the notice.
    rules_only_score: int | None = Field(default=None, ge=0, le=5)
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_reasons: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    explanation: str

    # The weighted sum before rounding and before any cap, kept so the arithmetic
    # can be checked. None when the weights were not used at all.
    weighted: float | None = None
    # The score the weights produced, before the caps lowered it.
    weighted_score: int | None = None
    # How many of the three axes the assessment established. The interface shows it
    # beside the score - "5 (1 of 3 axes established)" - because a score that rests
    # on one axis and one that rests on three are not the same claim.
    axes_established: int = Field(default=0, ge=0, le=3)
    # The two evidence counts the register orders on, from RulesEvidence. Carried
    # here and stored on the result so the ordering can be done in SQL.
    domain_strength_rank: int = Field(default=0, ge=0, le=3)
    domain_rules_matched: int = Field(default=0, ge=0)
    ai_enabled: bool = False
    # True when this result must not be stamped as current: the assessment failed
    # and the next run has to pick the notice up again.
    retry_next_run: bool = False
    policy_version: str

    @property
    def rules_priority(self) -> int:
        """What the register orders on: the rules grade where there is one."""
        return self.rules_only_score if self.rules_only_score is not None else self.score


def decide(
    tender: Tender,
    rules: RulesResult,
    *,
    policy: PolicyConfig,
    outcome: AssessmentOutcome | None = None,
    ai_enabled: bool = False,
) -> Decision:
    """Score and band one notice. Six paths, each with a defined outcome.

    1. Assessed normally: the weighted score, the caps, the band from the
       thresholds, then any review trigger.
    2. An exclusion matched with no domain evidence and no CPV match: the
       excluded_by_rule score, ARCHIVE, ``EXCLUDED_BY_RULE``. Not deleted.
    3. A model ran and the assessment did not come back: the assessment_failed
       score, REVIEW, ``ASSESSMENT_FAILED``, and ``retry_next_run`` set.
    4. No model, with domain or activity evidence: the graded rules-only score,
       REVIEW, ``RULES_ONLY``.
    5. No model and no evidence at all: the no_evidence score, ARCHIVE,
       ``RULES_ONLY_NO_EVIDENCE``.
    6. Assessed, and none of the three axes established: the
       insufficient_information score, REVIEW, ``INSUFFICIENT_INFORMATION``. The
       weights are not used at all - a number derived from nothing would read as
       a judgement, and there is no judgement here to read.

    Path 3 does not ask whether there was domain evidence. A notice with none
    still routes to the model - the rules stage rejects nothing - so a failure
    there is a failure whatever the rules found, and inventing a seventh case for
    it would mean archiving a notice nobody has read.
    """
    evidence = RulesEvidence.of(rules)
    assessment = outcome.assessment if outcome is not None else None
    axes = axis_scores(assessment)
    disagreement = _disagrees_on_domain(evidence, assessment, policy)

    confidence, reasons = confidence_for(
        tender,
        evidence=evidence,
        axes=axes,
        disagreement=disagreement,
        policy=policy,
        outcome=outcome,
    )

    if rules.route is RulesRoute.ARCHIVE_CANDIDATE:
        return _excluded(tender, evidence, policy, confidence, reasons, ai_enabled=ai_enabled)

    if not ai_enabled:
        return _rules_only(tender, rules, evidence, policy, confidence, reasons)

    if assessment is None:
        return _assessment_failed(tender, evidence, policy, confidence, reasons, outcome=outcome)

    if all(score is None for score in axes.values()):
        return _insufficient_information(tender, evidence, policy, confidence, reasons)

    return _assessed(
        tender,
        assessment,
        axes,
        evidence,
        policy,
        confidence,
        reasons,
        disagreement=disagreement,
    )


def axis_scores(assessment: Assessment | None) -> dict[str, int | None]:
    """The three axis scores by name. Every value is None when nothing was assessed."""
    if assessment is None:
        return dict.fromkeys(AXES)
    return {name: axis.score for name, axis in assessment.axes}


def weighted_sum(axes: Mapping[str, int | None], weights: Weights) -> Decimal | None:
    """The weighted score on the 0-5 scale, or None when no axis was established.

    Only the established axes are summed, and the total is divided by the weight
    they carry between them. An unknown axis therefore neither helps nor hurts:
    a notice judged only on domain scores what its domain scored. Treating it as
    a zero instead would archive notices for the sole reason that we do not know.
    """
    total = Decimal(0)
    used = Decimal(0)
    for axis, score in axes.items():
        if score is None:
            continue
        weight = weights.of(axis)
        total += weight * Decimal(score)
        used += weight

    return None if used == 0 else total / used


def round_half_up(value: Decimal) -> int:
    """Round to a whole number, .5 always upwards. Never Python's round()."""
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def rules_only_grade(evidence: RulesEvidence, policy: PolicyConfig) -> int:
    """How strong the deterministic evidence is, 0 to the rules-only ceiling.

    0 means no domain and no activity evidence at all. It is the group the recall
    audit samples: with no model, that is where a missed opportunity would hide.
    """
    grades = policy.rules_only

    if evidence.strongest_domain is RuleStrength.HIGH and evidence.has_activity:
        grade = grades.strong_domain_and_activity
    elif evidence.establishes_domain or (evidence.cpv_match and evidence.has_activity):
        grade = grades.partial_evidence
    elif evidence.has_activity or evidence.supporting_only:
        grade = grades.weak_evidence
    else:
        return 0

    return min(grade, grades.max_score)


def confidence_for(
    tender: Tender,
    *,
    evidence: RulesEvidence,
    axes: Mapping[str, int | None],
    disagreement: bool,
    policy: PolicyConfig,
    outcome: AssessmentOutcome | None = None,
) -> tuple[float, list[str]]:
    """How much weight this result can carry, and why, in plain language.

    Computed here from facts anyone can check on the notice itself, never asked
    of the model: a model's own estimate of how sure it is cannot be audited.

    Where the assessment stage already reported a lower number - a truncated
    notice, a missing judgement - that lower number wins. This stage may narrow
    confidence; it must never widen it.
    """
    settings = policy.confidence
    penalties = settings.penalties
    value = 1.0
    reasons: list[str] = []

    chars = _description_chars(tender)
    if chars < settings.description_chars_full:
        missing = 1.0 - chars / settings.description_chars_full
        value -= penalties.short_description * missing
        reasons.append(
            f"The notice description is {chars} characters, short of the "
            f"{settings.description_chars_full} a full one has, so there was little to read"
        )

    if settings.require_cpv and not tender.cpv_all:
        value -= penalties.missing_cpv
        reasons.append("The notice carries no CPV code, so there is no subject-matter code to read")

    for axis, score in axes.items():
        if score is None:
            value -= penalties.axis_unknown
            reasons.append(f"{_AXIS_LABELS[axis]} fit was not established")

    if settings.rules_model_agreement and disagreement:
        value -= penalties.rules_model_disagreement
        reasons.append(
            "The rules found "
            f"{_join(evidence.domain_rules)} in the buyer's own words and the assessment "
            "judged the notice out of domain"
        )

    if _missing_english_text(tender):
        value -= penalties.no_english_text
        reasons.append(
            "The notice has no English text, so it was read only in the language it was written in"
        )

    confidence = round(max(0.0, min(1.0, value)), 2)

    if outcome is not None and outcome.confidence < confidence:
        confidence = outcome.confidence
        reasons.extend(outcome.confidence_reasons)

    if not reasons:
        reasons.append(
            "Every axis was established, and the notice carries a full description and a CPV code"
        )

    return confidence, list(dict.fromkeys(reasons))


# ------------------------------------------------------------------- the paths


def _assessed(
    tender: Tender,
    assessment: Assessment,
    axes: Mapping[str, int | None],
    evidence: RulesEvidence,
    policy: PolicyConfig,
    confidence: float,
    reasons: list[str],
    *,
    disagreement: bool,
) -> Decision:
    """Path 1: assessed, weighted, capped, banded, then any review trigger.

    At least one axis is established here - the all-unknown case never reaches
    this function - so the weighted sum always has something to divide by.
    """
    weighted = weighted_sum(axes, policy.weights)
    if weighted is None:
        return _insufficient_information(tender, evidence, policy, confidence, reasons)
    from_weights = _clamp(round_half_up(weighted))

    score = from_weights
    codes: list[str] = []
    capped_by: list[str] = []
    for name, cap in policy.caps.items():
        if not cap.holds(axes):
            continue
        codes.append(name.upper())
        if cap.max_score < score:
            score = cap.max_score
            capped_by.append(name)

    codes.extend(name.upper() for name, tag in policy.reason_codes.items() if tag.holds(axes))

    band = policy.bands.band_for(score)
    triggers = _review_triggers(
        tender, assessment, policy, confidence=confidence, disagreement=disagreement
    )
    if triggers:
        band = Band.REVIEW
        codes.extend(triggers)

    return Decision(
        tender_id=tender.id,
        score=score,
        band=band,
        confidence=confidence,
        confidence_reasons=reasons,
        reason_codes=list(dict.fromkeys(codes)),
        explanation=_explain_assessed(
            assessment,
            weighted=weighted,
            from_weights=from_weights,
            score=score,
            capped_by=capped_by,
            confidence=confidence,
        ),
        weighted=float(weighted),
        weighted_score=from_weights,
        axes_established=sum(1 for score in axes.values() if score is not None),
        domain_strength_rank=evidence.strength_rank,
        domain_rules_matched=len(evidence.domain_rules),
        ai_enabled=True,
        policy_version=policy.policy_version,
    )


def _insufficient_information(
    tender: Tender,
    evidence: RulesEvidence,
    policy: PolicyConfig,
    confidence: float,
    reasons: list[str],
) -> Decision:
    """Path 6: the assessment ran and established none of the three axes.

    The weights are deliberately not used. Dividing nothing by nothing and
    clamping the result into the 1-5 range would put a number on the notice that
    looks exactly like a judgement and stands for the absence of one.
    """
    return Decision(
        tender_id=tender.id,
        score=policy.fallbacks.insufficient_information,
        band=Band.REVIEW,
        confidence=confidence,
        confidence_reasons=reasons,
        reason_codes=[INSUFFICIENT_INFORMATION],
        explanation=_explain_insufficient(policy, confidence=confidence),
        domain_strength_rank=evidence.strength_rank,
        domain_rules_matched=len(evidence.domain_rules),
        ai_enabled=True,
        policy_version=policy.policy_version,
    )


def _excluded(
    tender: Tender,
    evidence: RulesEvidence,
    policy: PolicyConfig,
    confidence: float,
    reasons: list[str],
    *,
    ai_enabled: bool,
) -> Decision:
    """Path 2: an exclusion matched and nothing spoke for the notice."""
    return Decision(
        tender_id=tender.id,
        score=policy.fallbacks.excluded_by_rule,
        band=Band.ARCHIVE,
        confidence=confidence,
        confidence_reasons=reasons,
        reason_codes=[EXCLUDED_BY_RULE],
        explanation=_explain_excluded(evidence, confidence=confidence),
        domain_strength_rank=evidence.strength_rank,
        domain_rules_matched=len(evidence.domain_rules),
        ai_enabled=ai_enabled,
        policy_version=policy.policy_version,
    )


def _assessment_failed(
    tender: Tender,
    evidence: RulesEvidence,
    policy: PolicyConfig,
    confidence: float,
    reasons: list[str],
    *,
    outcome: AssessmentOutcome | None,
) -> Decision:
    """Path 3: a model ran and this notice did not come back. Not a judgement."""
    return Decision(
        tender_id=tender.id,
        score=policy.fallbacks.assessment_failed,
        band=Band.REVIEW,
        confidence=confidence,
        confidence_reasons=reasons,
        reason_codes=[ASSESSMENT_FAILED],
        explanation=_explain_failed(outcome, confidence=confidence),
        domain_strength_rank=evidence.strength_rank,
        domain_rules_matched=len(evidence.domain_rules),
        ai_enabled=True,
        retry_next_run=True,
        policy_version=policy.policy_version,
    )


def _rules_only(
    tender: Tender,
    rules: RulesResult,
    evidence: RulesEvidence,
    policy: PolicyConfig,
    confidence: float,
    reasons: list[str],
) -> Decision:
    """Paths 4 and 5: no model is configured, so the evidence has to sort itself."""
    grade = rules_only_grade(evidence, policy)
    no_evidence = grade == 0

    return Decision(
        tender_id=tender.id,
        score=policy.rules_only.no_evidence if no_evidence else grade,
        band=Band.ARCHIVE if no_evidence else Band.REVIEW,
        rules_only_score=grade,
        confidence=confidence,
        confidence_reasons=reasons,
        reason_codes=[RULES_ONLY_NO_EVIDENCE if no_evidence else RULES_ONLY],
        explanation=_explain_rules_only(
            rules, evidence, policy, grade=grade, confidence=confidence
        ),
        domain_strength_rank=evidence.strength_rank,
        domain_rules_matched=len(evidence.domain_rules),
        ai_enabled=False,
        policy_version=policy.policy_version,
    )


def _review_triggers(
    tender: Tender,
    assessment: Assessment,
    policy: PolicyConfig,
    *,
    confidence: float,
    disagreement: bool,
) -> list[str]:
    """The facts that put a notice in front of a person, whatever it scored."""
    triggers = policy.review_triggers
    codes: list[str] = []

    unknown = [name for name, axis in assessment.axes if axis.score is None]
    if triggers.axis_unknown and unknown:
        codes.append(INSUFFICIENT_INFORMATION if len(unknown) == len(AXES) else AXIS_UNKNOWN)

    if triggers.missing_information and assessment.missing_information:
        codes.append(MISSING_INFORMATION)

    if triggers.low_confidence and confidence < policy.confidence.review_below:
        codes.append(LOW_CONFIDENCE)

    if triggers.rules_model_disagreement and disagreement:
        codes.append(RULES_MODEL_DISAGREEMENT)

    if triggers.multi_lot and tender.multi_lot:
        codes.append(MULTI_LOT)

    return codes


def _disagrees_on_domain(
    evidence: RulesEvidence,
    assessment: Assessment | None,
    policy: PolicyConfig,
) -> bool:
    """True when the rules established a domain and the assessment did not find one.

    Only in that direction. The rules finding nothing where the model finds a
    domain is the normal case, not a conflict: our vocabulary is English and TED
    titles arrive in every EU language.
    """
    if assessment is None or not evidence.establishes_domain:
        return False

    domain = assessment.domain_fit
    if domain.score is None:
        return False
    return domain.score <= policy.confidence.disagreement_domain_max


# --------------------------------------------------------------- explanations


def _explain_assessed(
    assessment: Assessment,
    *,
    weighted: Decimal,
    from_weights: int,
    score: int,
    capped_by: list[str],
    confidence: float,
) -> str:
    """Assembled from the parts that produced the score. Never generated."""
    parts = []
    established = 0
    for name, axis in assessment.axes:
        label = _AXIS_LABELS[name]
        if axis.score is None:
            parts.append(f"{label}: not established.")
        else:
            established += 1
            parts.append(f"{label}: {_readable(axis.label)} {axis.score}/5.")

    parts.append(f"Weighted {_number(weighted)} -> {from_weights}.")
    if established < len(AXES):
        parts.append(
            f"Weighed on {established} of {len(AXES)} axes; the rest were not established "
            "and were left out rather than counted as zero."
        )

    if capped_by:
        parts.append(f"Capped to {score} ({_join([_readable(name) for name in capped_by])}).")

    parts.append(f"Confidence {_fraction(confidence)}.")

    quotes = _quotes(assessment)
    if quotes:
        parts.append("Evidence: " + "; ".join(f"'{quote}'" for quote in quotes) + ".")

    return " ".join(parts)


def _explain_insufficient(policy: PolicyConfig, *, confidence: float) -> str:
    return (
        "Domain: not established. Service: not established. Stage: not established. "
        "The notice was assessed and none of the three axes could be established from its "
        "text, so no weighted score was calculated - it is recorded at "
        f"{policy.fallbacks.insufficient_information} and held for review. Thin text means "
        f"we do not know; it never means no. Confidence {_fraction(confidence)}."
    )


def _explain_excluded(evidence: RulesEvidence, *, confidence: float) -> str:
    named = _join(evidence.exclusion_rules) or "an exclusion rule"
    return (
        f"Excluded by rule: {named} matched, no domain rule matched and no subject-matter CPV "
        f"code matched, so no assessment was made. Confidence {_fraction(confidence)}. "
        "Archived means low priority: the notice is kept, stays searchable and can be found "
        "again."
    )


def _explain_failed(outcome: AssessmentOutcome | None, *, confidence: float) -> str:
    error = (outcome.error if outcome is not None else None) or "the assessment did not run"
    return (
        f"The assessment did not complete: {error}. Nothing has been judged about this notice, "
        "so it is held for review and left unscreened at the current versions - the next run "
        f"will try it again. Confidence {_fraction(confidence)}."
    )


def _explain_rules_only(
    rules: RulesResult,
    evidence: RulesEvidence,
    policy: PolicyConfig,
    *,
    grade: int,
    confidence: float,
) -> str:
    parts = [
        "Screened on the keyword rules alone: no model is configured, so nothing has judged "
        "what this contract is for."
    ]

    if evidence.establishes_domain:
        strongest = evidence.strongest_domain.value if evidence.strongest_domain else "none"
        parts.append(f"Domain evidence: {_join(evidence.domain_rules)} (strongest {strongest}).")
    elif evidence.supporting_only:
        parts.append("Supporting energy terms only, which never establish a domain on their own.")
    else:
        parts.append(
            "No domain rule matched, which is not evidence against the notice: our vocabulary "
            "is English and this notice may not be."
        )

    parts.append(
        "An activity rule matched." if evidence.has_activity else "No activity rule matched."
    )
    if evidence.cpv_match:
        parts.append("A subject-matter CPV code matched.")

    if grade == 0:
        parts.append(
            f"No domain or activity evidence at all, so it is recorded at "
            f"{policy.rules_only.no_evidence} and archived as low priority."
        )
    else:
        parts.append(
            f"Rules-only score {grade} of {policy.rules_only.max_score}, held for review - "
            "a shortlist would claim a judgement nothing has made."
        )

    parts.append(f"Confidence {_fraction(confidence)}.")

    quote = next((match.evidence for match in rules.matches if match.evidence.strip()), None)
    if quote:
        parts.append(f"Evidence: '{quote}'.")

    return " ".join(parts)


# ------------------------------------------------------------------ internals


def _clamp(score: int) -> int:
    return max(1, min(5, score))


def _description_chars(tender: Tender) -> int:
    """How much description there is to read, across every language variant."""
    return sum(
        len(block.text.strip())
        for block in tender.screening_blocks
        if not block.field.lower().startswith("title")
    )


def _missing_english_text(tender: Tender) -> bool:
    """True when the notice has text and none of it is in English."""
    blocks = [block for block in tender.screening_blocks if block.text.strip()]
    if not blocks:
        return False
    return not any((block.language or "").strip().lower() in _ENGLISH for block in blocks)


def _quotes(assessment: Assessment) -> list[str]:
    """The first quote from each established axis, deduplicated and kept short."""
    found: list[str] = []
    for _, axis in assessment.axes:
        quotes = axis.quotes
        if quotes:
            found.append(quotes[0].strip())
    return list(dict.fromkeys(found))[:_QUOTES_IN_EXPLANATION]


def _readable(value: str) -> str:
    """A stored vocabulary value as a person would say it."""
    return str(value).replace("_", " ").strip().lower()


def _join(values: Sequence[str]) -> str:
    return ", ".join(values)


def _number(value: Decimal) -> str:
    """A weighted score with no trailing zeros: 4.80 reads as 4.8, 5.00 as 5."""
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP).normalize()
    return f"{quantized:f}"


def _fraction(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


__all__ = [
    "ASSESSMENT_FAILED",
    "AXES",
    "AXIS_UNKNOWN",
    "DEFAULT_POLICY_PATH",
    "EXCLUDED_BY_RULE",
    "INSUFFICIENT_INFORMATION",
    "LOW_CONFIDENCE",
    "MISSING_INFORMATION",
    "MULTI_LOT",
    "RANKING_FIELDS",
    "RULES_MODEL_DISAGREEMENT",
    "RULES_ONLY",
    "RULES_ONLY_NO_EVIDENCE",
    "Bands",
    "Cap",
    "Condition",
    "ConditionError",
    "Decision",
    "PolicyConfig",
    "RulesEvidence",
    "Tag",
    "Weights",
    "axis_scores",
    "compile_condition",
    "confidence_for",
    "decide",
    "load_policy",
    "parse_policy",
    "ranking_key",
    "round_half_up",
    "rules_only_grade",
    "weighted_sum",
]
