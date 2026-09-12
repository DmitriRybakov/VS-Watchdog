"""The assessment stage: what a language model is allowed to say about a notice.

What this stage is for, and what it is not for:

- It produces **three independent judgements with quotes**, not a score and not a
  verdict. Combining the axes into a final score is the policy stage's job, and
  it reads ``docs/SCREENING_POLICY.md``.
- It assesses only what the rules stage routed ``ASSESS``. An ``ARCHIVE_CANDIDATE``
  is not rejected here either - it is simply not worth a model call.
- **One failing notice never stops a batch.** The failure is recorded against the
  notice and against the run, and the notice is left unassessed so a later run
  picks it up. A notice we could not judge is never an archived notice.

What is sent, and what deliberately is not:

- Sent: the buyer's own title and description blocks, exactly as stored, in their
  original languages, plus the notice type and the contract nature.
- Not sent: geography, contract value, currency, deadline, buyer name, links and
  attachments. Those change the bid route, never the relevance score, and sending
  them invites the model to let them touch the three axes - the specific failure
  the governing principle in ``config/profile.yaml`` exists to prevent.
- Not sent: TED's composed display title, which carries a country name and TED's
  own CPV label rather than the buyer's words. See docs/decisions/0002.
- Not sent: CPV codes. They are a recall filter, not a relevance signal; see
  docs/decisions/0004.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from watchdog.core.enums import RulesRoute
from watchdog.core.logging import get_logger
from watchdog.core.models import Assessment, RulesResult, Tender, TextBlock
from watchdog.llm.cache import ResponseCache, cache_key
from watchdog.llm.provider import (
    LLMError,
    LLMProvider,
    ProviderDisabled,
    Usage,
    input_hash,
)
from watchdog.screening.profile import Profile
from watchdog.screening.prompts import DEFAULT_PROMPT_VERSION, render_system_prompt
from watchdog.screening.rules import normalise

log = get_logger(__name__)

# How much notice text one call may carry. The title is never counted out.
DEFAULT_NOTICE_CHARS = 6000

# The ceiling on parallel calls, whatever a caller asks for. Four is enough to
# keep a batch moving and low enough not to trip a provider's rate limit.
MAX_CONCURRENCY = 4

# How often a long batch says where it has got to.
PROGRESS_EVERY = 25

# Shown in place of the text that did not fit, so a short answer can be read as
# "the model was given less" rather than "the notice said little".
TRUNCATION_MARKER = "\n[... the rest of this notice was too long to send]"

# Under this many characters of notice text, "we do not know" is the expected
# answer and the confidence says so. It is never evidence of irrelevance.
THIN_TEXT_CHARS = 200

# How the three axes are named on screen, from the names the schema uses.
_AXIS_NAMES = {
    "domain_fit": "Domain fit",
    "service_fit": "Service fit",
    "stage_fit": "Stage fit",
}

# How much of a fabricated quote to name in the repair message, and how many. The
# model cannot see its own rejected answer, so it has to be told which phrase to
# stop inventing - but there is no point sending back a paragraph of it.
_QUOTE_IN_ERROR = 80
_QUOTES_IN_ERROR = 3

# What a model uses to abbreviate a long sentence it is quoting.
_ELLIPSES = ("\u2026", "...")


@dataclass(frozen=True)
class Candidate:
    """One tender and what the rules stage found on it.

    The two travel together because the route decides whether the notice is worth
    a call, and the caller has both in hand already.
    """

    tender: Tender
    rules: RulesResult

    @property
    def routed_to_assess(self) -> bool:
        return self.rules.route is RulesRoute.ASSESS


@dataclass(frozen=True)
class Prompt:
    """Exactly what one call sends, kept so it can be inspected and hashed."""

    system: str
    user: str
    truncated: bool

    @property
    def input_hash(self) -> str:
        return input_hash(self.system, self.user)


@dataclass(frozen=True)
class AssessmentOutcome:
    """What the stage got for one notice, whether or not that is an assessment.

    ``assessment`` is None when the call failed or the notice had no text to send.
    That is "not established", never "not relevant": the notice stays unassessed
    and a later run tries again.
    """

    tender_id: str
    title: str
    assessment: Assessment | None
    usage: Usage
    input_hash: str
    truncated: bool
    confidence: float
    confidence_reasons: tuple[str, ...]
    error: str | None = None
    error_type: str | None = None

    @property
    def ok(self) -> bool:
        return self.assessment is not None

    @property
    def cached(self) -> bool:
        return self.usage.cached


@dataclass(frozen=True)
class AssessRun:
    """What one batch did, in the shape the CLI and the web layer report it."""

    provider: str
    model: str | None
    prompt_version: str
    ai_enabled: bool
    results: tuple[AssessmentOutcome, ...] = ()
    # Ids the rules stage routed away from the model. Not rejections.
    skipped: tuple[str, ...] = ()

    @property
    def assessed(self) -> tuple[AssessmentOutcome, ...]:
        return tuple(item for item in self.results if item.ok)

    @property
    def failed(self) -> tuple[AssessmentOutcome, ...]:
        return tuple(item for item in self.results if not item.ok)

    @property
    def tokens_in(self) -> int:
        return sum(item.usage.billable_tokens_in for item in self.results)

    @property
    def tokens_out(self) -> int:
        return sum(item.usage.billable_tokens_out for item in self.results)

    @property
    def latency_ms(self) -> int:
        return sum(item.usage.latency_ms for item in self.results)

    @property
    def errors(self) -> tuple[str, ...]:
        return tuple(
            f"{item.tender_id}: {item.error}" for item in self.failed if item.error is not None
        )

    @property
    def counts(self) -> dict[str, int]:
        return {
            "considered": len(self.results) + len(self.skipped),
            "assessed": len(self.assessed),
            "cached": sum(1 for item in self.results if item.cached),
            "repaired": sum(1 for item in self.results if item.usage.repaired),
            "failed": len(self.failed),
            "skipped": len(self.skipped),
        }


ProgressCallback = Callable[[int, int], None]


def assess(
    candidates: Sequence[Candidate],
    *,
    provider: LLMProvider,
    profile: Profile,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    cache: ResponseCache | None = None,
    notice_chars: int = DEFAULT_NOTICE_CHARS,
    concurrency: int = MAX_CONCURRENCY,
    on_progress: ProgressCallback | None = None,
) -> AssessRun:
    """Assess every candidate the rules stage routed to the model.

    With the disabled provider this does nothing, logs once and returns an empty
    run. It never raises for that: running with no model is a supported way to use
    the tool, not an error condition.
    """
    routed = [item for item in candidates if item.routed_to_assess]
    skipped = tuple(item.tender.id for item in candidates if not item.routed_to_assess)

    if not provider.enabled:
        log.info(
            "assessment_disabled",
            provider=provider.name,
            candidates=len(candidates),
            reason="no model is configured; these notices are screened on the rules alone",
        )
        return AssessRun(
            provider=provider.name,
            model=provider.model,
            prompt_version=prompt_version,
            ai_enabled=False,
            skipped=tuple(item.tender.id for item in candidates),
        )

    system = render_system_prompt(mandate=profile.mandate_text(), version=prompt_version)
    counter = _Counter(total=len(routed), on_progress=on_progress)
    workers = max(1, min(concurrency, MAX_CONCURRENCY))

    def work(candidate: Candidate) -> AssessmentOutcome:
        outcome = _assess_one(
            candidate.tender,
            provider=provider,
            system=system,
            prompt_version=prompt_version,
            cache=cache,
            notice_chars=notice_chars,
        )
        counter.done()
        return outcome

    if not routed:
        results: list[AssessmentOutcome] = []
    elif workers == 1:
        results = [work(candidate) for candidate in routed]
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="assess") as pool:
            # map keeps the input order, so a listing reads the same every time.
            results = list(pool.map(work, routed))

    run = AssessRun(
        provider=provider.name,
        model=provider.model,
        prompt_version=prompt_version,
        ai_enabled=True,
        results=tuple(results),
        skipped=skipped,
    )
    log.info("assessment_finished", provider=provider.name, model=provider.model, **run.counts)
    return run


def build_prompt(
    tender: Tender,
    *,
    system: str,
    notice_chars: int = DEFAULT_NOTICE_CHARS,
) -> Prompt:
    """The two messages for one notice. Pure, so what is sent can be read in a test."""
    user, truncated = build_notice_text(tender, budget=notice_chars)
    return Prompt(system=system, user=user, truncated=truncated)


def build_notice_text(tender: Tender, *, budget: int = DEFAULT_NOTICE_CHARS) -> tuple[str, bool]:
    """The notice as the model sees it, and whether anything had to be left out.

    Title blocks are never cut. Losing the end of a description costs detail;
    losing the title costs the subject of the contract.
    """
    header = [
        "NOTICE",
        f"Notice type: {tender.notice_stage.value}",
        f"Contract type: {_contract_types(tender)}",
    ]
    titles = [_labelled(block) for block in _title_blocks(tender)]
    body = [_labelled(block) for block in _body_blocks(tender)]

    fixed = "\n\n".join([*header, *titles])
    remaining = budget - len(fixed)

    kept: list[str] = []
    truncated = False
    for piece in body:
        if remaining <= len(TRUNCATION_MARKER):
            truncated = True
            break
        if len(piece) + 2 <= remaining:
            kept.append(piece)
            remaining -= len(piece) + 2
            continue

        kept.append(_cut(piece, remaining - len(TRUNCATION_MARKER)) + TRUNCATION_MARKER)
        truncated = True
        break

    return "\n\n".join([fixed, *kept]) if kept else fixed, truncated


def has_text_to_judge(tender: Tender) -> bool:
    """False when there is nothing for a model to read. Not a judgement about the notice."""
    return any(block.text.strip() for block in _screening_blocks(tender))


def check_quotes_are_grounded(assessment: Assessment, *, notice: str) -> None:
    """Reject an evidence quote that does not occur in the text we sent.

    Well-formed JSON containing a phrase the notice never used is a fabrication
    that looks exactly like evidence, and is worse than no quote at all: it is the
    one thing a colleague cannot check without opening the notice. Raising here
    sends the answer back for its one repair attempt.

    Compared after the same normalisation the rules stage matches on - case,
    accents, dashes and runs of whitespace - so a model that tidies a quote is
    forgiven while a model that invents one is not. A quote split by an ellipsis
    is checked a piece at a time, which is how a model abbreviates a long sentence.
    """
    haystack = normalise(notice).text
    ungrounded = [
        (name, quote)
        for name, axis in assessment.axes
        for quote in axis.quotes
        if not _occurs_in(quote, haystack)
    ]
    if not ungrounded:
        return

    named = "; ".join(
        f"{name}: {quote[:_QUOTE_IN_ERROR]!r}" for name, quote in ungrounded[:_QUOTES_IN_ERROR]
    )
    raise ValueError(
        f"these evidence quotes do not occur in the notice text and appear to be invented - "
        f"{named}; every quote must be copied from the notice, or the axis must be null"
    )


def confidence_for(
    assessment: Assessment | None,
    *,
    notice_chars: int,
    truncated: bool,
    cached: bool,
) -> tuple[float, tuple[str, ...]]:
    """How much weight the assessment can carry, and why, in plain language.

    This is the assessment stage's own confidence: it is about how well evidenced
    the three judgements are, not about how relevant the notice is. The policy
    stage may narrow it further; it must never widen it.

    There is no penalty here for a scored axis without a quote. That is not a weak
    answer but a rejected one: the schema refuses it and the repair attempt asks
    again.
    """
    if assessment is None:
        return 0.0, ("The notice could not be assessed, so nothing has been established",)

    reasons: list[str] = []
    score = 1.0

    for name, axis in assessment.axes:
        if axis.score is None:
            score -= 0.20
            reasons.append(f"{_AXIS_NAMES[name]} could not be established from the notice text")

    if notice_chars < THIN_TEXT_CHARS:
        score -= 0.15
        reasons.append(
            f"The notice text is short ({notice_chars} characters), so there was little to read"
        )

    if truncated:
        score -= 0.05
        reasons.append("The notice was longer than one assessment can send, so part was not read")

    if assessment.missing_information:
        score -= 0.10
        reasons.append(
            f"The model listed {len(assessment.missing_information)} thing(s) the notice does "
            "not say"
        )

    if not reasons:
        reasons.append("All three judgements were made and each one is quoted from the notice")

    return round(max(0.0, min(1.0, score)), 2), tuple(reasons)


# ------------------------------------------------------------------- internals


def _assess_one(
    tender: Tender,
    *,
    provider: LLMProvider,
    system: str,
    prompt_version: str,
    cache: ResponseCache | None,
    notice_chars: int,
) -> AssessmentOutcome:
    """One notice. Never raises: a failure here is a recorded fact, not an abort."""
    prompt = build_prompt(tender, system=system, notice_chars=notice_chars)

    if not has_text_to_judge(tender):
        return _unassessed(
            tender,
            prompt,
            error="the notice has no title or description text to judge",
            error_type="NoNoticeText",
        )

    key = cache_key(
        provider=provider.name,
        model=provider.model,
        prompt_version=prompt_version,
        payload=f"{prompt.system}\n{prompt.user}",
    )

    try:
        assessment, usage = _answer(provider, prompt, cache=cache, key=key)
    except ProviderDisabled as exc:
        return _unassessed(tender, prompt, error=str(exc), error_type="ProviderDisabled")
    except LLMError as exc:
        log.warning(
            "assessment_failed",
            tender_id=tender.id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return _unassessed(tender, prompt, error=str(exc), error_type=type(exc).__name__)

    confidence, reasons = confidence_for(
        assessment,
        notice_chars=len(prompt.user),
        truncated=prompt.truncated,
        cached=usage.cached,
    )
    return AssessmentOutcome(
        tender_id=tender.id,
        title=_display_title(tender),
        assessment=assessment,
        usage=usage,
        input_hash=prompt.input_hash,
        truncated=prompt.truncated,
        confidence=confidence,
        confidence_reasons=reasons,
    )


def _answer(
    provider: LLMProvider,
    prompt: Prompt,
    *,
    cache: ResponseCache | None,
    key: str,
) -> tuple[Assessment, Usage]:
    """The cached answer if there is one, otherwise a call, which is then cached."""

    def check(assessment: Assessment) -> None:
        check_quotes_are_grounded(assessment, notice=prompt.user)

    if cache is not None:
        hit = cache.get(key)
        if hit is not None:
            try:
                cached = Assessment.model_validate_json(hit.content)
                check(cached)
            except ValueError:
                # Written by an older build, under an older schema or an older
                # check. Treat it as a miss and call again.
                log.info("llm_cache_entry_ignored", reason="does not meet the current contract")
            else:
                return cached, hit.usage

    assessment, usage = provider.assess(
        system=prompt.system, user=prompt.user, schema=Assessment, check=check
    )

    if cache is not None:
        cache.put(key, content=assessment.model_dump_json(), usage=usage)

    return assessment, usage


def _unassessed(
    tender: Tender, prompt: Prompt, *, error: str, error_type: str
) -> AssessmentOutcome:
    confidence, reasons = confidence_for(
        None, notice_chars=len(prompt.user), truncated=prompt.truncated, cached=False
    )
    return AssessmentOutcome(
        tender_id=tender.id,
        title=_display_title(tender),
        assessment=None,
        usage=Usage(attempts=0),
        input_hash=prompt.input_hash,
        truncated=prompt.truncated,
        confidence=confidence,
        confidence_reasons=reasons,
        error=error,
        error_type=error_type,
    )


class _Counter:
    """Counts finished notices across threads and says so every 25."""

    def __init__(self, *, total: int, on_progress: ProgressCallback | None) -> None:
        self._lock = threading.Lock()
        self._done = 0
        self._total = total
        self._on_progress = on_progress

    def done(self) -> None:
        with self._lock:
            self._done += 1
            reached = self._done

        if reached % PROGRESS_EVERY and reached != self._total:
            return

        log.info("assessment_progress", assessed=reached, of=self._total)
        if self._on_progress is not None:
            self._on_progress(reached, self._total)


def _screening_blocks(tender: Tender) -> list[TextBlock]:
    """The stored blocks, or the buyer's own title if there are none.

    A tender mapped before blocks existed still has a native title, and one line
    of the buyer's own words is worth more than nothing.
    """
    if tender.screening_blocks:
        return list(tender.screening_blocks)
    if tender.title_native:
        return [
            TextBlock(
                field="title-proc",
                language=tender.title_native_language,
                text=tender.title_native,
            )
        ]
    return []


def _title_blocks(tender: Tender) -> list[TextBlock]:
    return [block for block in _screening_blocks(tender) if block.field.lower().startswith("title")]


def _body_blocks(tender: Tender) -> list[TextBlock]:
    return [
        block
        for block in _screening_blocks(tender)
        if not block.field.lower().startswith("title") and block.text.strip()
    ]


def _labelled(block: TextBlock) -> str:
    return f"{block.label}\n{block.text.strip()}"


def _occurs_in(quote: str, haystack: str) -> bool:
    """True when every part of ``quote`` is in ``haystack``, both normalised."""
    for ellipsis in _ELLIPSES:
        quote = quote.replace(ellipsis, "\x00")

    pieces = [normalise(piece).text for piece in quote.split("\x00")]
    return all(piece in haystack for piece in pieces if piece)


def _contract_types(tender: Tender) -> str:
    natures = [nature.value for nature in tender.contract_natures]
    return ", ".join(natures) if natures else tender.contract_nature.value


def _cut(text: str, limit: int) -> str:
    """Cut at the last space before ``limit``, so a word is never left half written."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text

    window = text[:limit]
    space = window.rfind(" ")
    return window[:space].rstrip() if space > limit // 2 else window.rstrip()


def _display_title(tender: Tender) -> str:
    """What a person should see in a listing: the buyer's own words where we have them."""
    return tender.title_native or tender.title


__all__ = [
    "DEFAULT_NOTICE_CHARS",
    "MAX_CONCURRENCY",
    "AssessRun",
    "AssessmentOutcome",
    "Candidate",
    "Prompt",
    "assess",
    "build_notice_text",
    "build_prompt",
    "check_quotes_are_grounded",
    "confidence_for",
    "has_text_to_judge",
]
