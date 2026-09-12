"""A provider that answers from canned text. For tests and demos, never for real work.

It talks to nothing. What it returns is decided by matching a marker against the
notice text in the user message, which keeps a test readable - "the floating wind
notice answers like this, the medical one answers badly" - and keeps a demo
identical every time it is run.

A marker may be given a list of answers instead of one. Successive calls consume
them in order and the last one repeats, which is how the single repair attempt is
exercised: give it a malformed answer followed by a good one.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel

from watchdog.core.enums import DecisionStage, Domain, ServiceType
from watchdog.core.models import Assessment, AxisScore
from watchdog.llm.provider import ProviderNotConfigured, Usage, parse_with_repair, timed

# One canned answer: raw text as the model would return it, a model object that
# is serialised for us, or an exception to raise instead of answering.
Answer = str | BaseModel | Exception
# What a marker maps to. A sequence is consumed in order; the last entry repeats.
AnswerSpec = Answer | Sequence[Answer]

# Roughly four characters to a token. Good enough to make a cost visible in a
# test; nothing here is a real count.
_CHARS_PER_TOKEN = 4

# What the built-in demo answer looks for, and what it concludes. Deliberately
# crude: it exists so that a demo without a model still shows a plausible
# register, not to be a second screening engine.
_DEMO_DOMAINS: tuple[tuple[str, Domain], ...] = (
    ("hydrogen", Domain.HYDROGEN),
    ("carbon capture", Domain.CCS_CO2),
    ("offshore wind", Domain.OFFSHORE_WIND),
    ("floating wind", Domain.OFFSHORE_WIND),
    ("solar", Domain.ONSHORE_RENEWABLES),
    ("battery", Domain.ENERGY_STORAGE),
    ("substation", Domain.GRID_TRANSMISSION),
    ("grid", Domain.GRID_TRANSMISSION),
    ("biofuel", Domain.RENEWABLE_FUELS),
    ("decarbonisation", Domain.INDUSTRIAL_DECARB),
)

_DEMO_SERVICES: tuple[tuple[str, ServiceType, int], ...] = (
    ("feasibility", ServiceType.EARLY_PHASE_STUDY, 5),
    ("concept study", ServiceType.EARLY_PHASE_STUDY, 5),
    ("advisory", ServiceType.ADVISORY, 4),
    ("consultancy", ServiceType.ADVISORY, 4),
    ("due diligence", ServiceType.DUE_DILIGENCE, 4),
    ("construction", ServiceType.EXECUTION_EPC, 1),
    ("installation", ServiceType.EXECUTION_EPC, 1),
    ("maintenance", ServiceType.OPERATIONS, 0),
)

_DEMO_STAGES: tuple[tuple[str, DecisionStage, int], ...] = (
    ("pre-feed", DecisionStage.PRE_FEED_FEASIBILITY, 5),
    ("feasibility", DecisionStage.PRE_FEED_FEASIBILITY, 5),
    ("concept", DecisionStage.CONCEPT_OPTION, 5),
    ("feed", DecisionStage.FEED_DEFINITION, 3),
    ("construction", DecisionStage.EXECUTION, 1),
    ("operation", DecisionStage.OPERATIONS, 0),
)


@dataclass(frozen=True)
class FakeCall:
    """One call the fake was asked to answer, kept so a test can count them."""

    system: str
    user: str
    marker: str | None


@dataclass
class FakeProvider:
    """Deterministic canned answers. Same input, same output, no network."""

    name: str = "fake"
    model: str | None = "fake-1"
    enabled: bool = True
    # Marker text, matched case-insensitively against the user message, in order.
    responses: Mapping[str, AnswerSpec] = field(default_factory=dict)
    # Used when no marker matches. None means "build the demo answer".
    default: AnswerSpec | None = None
    calls: list[FakeCall] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._used: dict[str, int] = {}

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def assess[SchemaT: BaseModel](
        self,
        *,
        system: str,
        user: str,
        schema: type[SchemaT],
        check: Callable[[SchemaT], None] | None = None,
    ) -> tuple[SchemaT, Usage]:
        return parse_with_repair(self._send, system=system, user=user, schema=schema, check=check)

    def _send(self, system: str, user: str) -> tuple[str, Usage]:
        return timed(lambda: self._answer(system, user))

    def _answer(self, system: str, user: str) -> tuple[str, Usage]:
        with self._lock:
            marker = self._marker_for(user)
            answer = self._next_answer(marker, user)
            self.calls.append(FakeCall(system=system, user=user, marker=marker))

        if isinstance(answer, Exception):
            raise answer

        raw = answer if isinstance(answer, str) else answer.model_dump_json()
        usage = Usage(
            tokens_in=(len(system) + len(user)) // _CHARS_PER_TOKEN,
            tokens_out=len(raw) // _CHARS_PER_TOKEN,
        )
        return raw, usage

    def _marker_for(self, user: str) -> str | None:
        haystack = user.casefold()
        for marker in self.responses:
            if marker.casefold() in haystack:
                return marker
        return None

    def _next_answer(self, marker: str | None, user: str) -> Answer:
        spec = self.responses[marker] if marker is not None else self.default
        if spec is None:
            return demo_assessment(user)

        if isinstance(spec, str | BaseModel | Exception):
            return spec

        answers = list(spec)
        if not answers:
            raise ProviderNotConfigured(
                f"the fake provider was given an empty list of answers for {marker!r}"
            )

        key = marker or ""
        index = min(self._used.get(key, 0), len(answers) - 1)
        self._used[key] = index + 1
        return answers[index]


def demo_assessment(text: str) -> Assessment:
    """A plausible answer built from the words in the notice. For demos only.

    An axis it finds nothing for is left unknown rather than scored low. Not to be
    polite: a scored axis with no quote behind it is refused by the schema, which
    is the same rule a real model answer is held to.
    """
    haystack = text.casefold()

    domain_label: Domain = Domain.UNKNOWN
    domain_score: int | None = None
    domain_evidence: list[str] = []
    for word, domain in _DEMO_DOMAINS:
        if word in haystack:
            domain_label, domain_score = domain, 5
            domain_evidence = [_quote(text, word)]
            break

    service_label: ServiceType = ServiceType.UNKNOWN
    service_score: int | None = None
    service_evidence: list[str] = []
    for word, label, score in _DEMO_SERVICES:
        if word in haystack:
            service_label, service_score = label, score
            service_evidence = [_quote(text, word)]
            break

    stage_label: DecisionStage = DecisionStage.UNKNOWN
    stage_score: int | None = None
    stage_evidence: list[str] = []
    for word, stage, score in _DEMO_STAGES:
        if word in haystack:
            stage_label, stage_score = stage, score
            stage_evidence = [_quote(text, word)]
            break

    missing = [
        name
        for name, score in (
            ("domain_fit", domain_score),
            ("service_fit", service_score),
            ("stage_fit", stage_score),
        )
        if score is None
    ]

    return Assessment(
        domain_fit=AxisScore[Domain](
            score=domain_score, label=domain_label, evidence=domain_evidence
        ),
        service_fit=AxisScore[ServiceType](
            score=service_score, label=service_label, evidence=service_evidence
        ),
        stage_fit=AxisScore[DecisionStage](
            score=stage_score, label=stage_label, evidence=stage_evidence
        ),
        missing_information=[f"the notice does not say enough to judge {name}" for name in missing],
        short_reason="Canned answer from the fake provider. No model was called.",
    )


def _quote(text: str, word: str) -> str:
    """The words around ``word``, cut from the original text exactly as written."""
    start = text.casefold().find(word.casefold())
    if start < 0:
        return ""
    return text[max(0, start - 40) : start + len(word) + 40].strip()
