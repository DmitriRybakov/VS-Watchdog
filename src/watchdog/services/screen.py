"""Screening operations the CLI and the web layer can call.

Three stages run here, in order, and each one is a different kind of claim:

1. the deterministic rules, which produce **evidence** and reject nothing;
2. the model assessment, which produces **three judgements with quotes**, and is
   skipped entirely when no provider is configured;
3. the scoring policy, which produces **one score, one band and the reasons**, and
   is the only stage that writes a screening result.

``assess_sample`` runs stages 1 and 2 and stores nothing: it exists so that a
person can read twenty assessments before anything is banded on them.
``screen_register`` runs all three and persists.

The configuration comes from the active version in the database, taken as one
:class:`~watchdog.services.configuration.ConfigSnapshot` at the start of a run and
held to the end. That is deliberate: a run screens thousands of notices over many
minutes, and if somebody saved a rules change halfway through, half the results
would be stamped with one version and half with another, with nothing on screen
to say where the line fell.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from watchdog.core.enums import Band, RunKind, RunStatus
from watchdog.core.logging import get_logger
from watchdog.core.models import (
    ScreeningResult,
    ScreeningVersions,
    Tender,
    TenderPage,
)
from watchdog.core.settings import Settings, get_settings
from watchdog.llm.cache import ResponseCache
from watchdog.llm.provider import LLMError, LLMProvider, get_provider
from watchdog.screening.assess import AssessmentOutcome, AssessRun, Candidate, assess
from watchdog.screening.policy import (
    Decision,
    RulesEvidence,
    decide,
    ranking_key,
)
from watchdog.screening.prompts import DEFAULT_PROMPT_VERSION
from watchdog.screening.rules import RuleEngine
from watchdog.services import configuration as configuration_service
from watchdog.services.configuration import ConfigSnapshot
from watchdog.services.ingest import DatabaseNotReady
from watchdog.storage.db import get_session_factory
from watchdog.storage.repository import MAX_PAGE_SIZE, Repository

log = get_logger(__name__)

# Re-exported so cli.py and the web layer can name the failure without importing
# the llm package, which they are not allowed to do.
__all__ = [
    "AssessmentOutcome",
    "LLMError",
    "Ranked",
    "SampleOutcome",
    "ScreenOutcome",
    "assess_sample",
    "screen_register",
]

# How many notices to read from the register for each one we hope to assess. The
# rules stage routes some away, so asking for exactly the sample size would
# usually return fewer than asked for.
_CANDIDATE_OVERSAMPLE = 4

# How many notices one screening batch holds in memory at a time.
_BATCH = 200

# How many rows the run report shows at the top of the register.
TOP_ROWS = 20


@dataclass(frozen=True)
class SampleOutcome:
    """One `watchdog screen --stage 2` run, in the shape the CLI reports it."""

    run: AssessRun
    run_id: str | None
    # How many notices were read from the register before the rules stage filtered.
    considered: int
    rules_version: str
    profile_version: str
    cache_enabled: bool

    @property
    def ok(self) -> bool:
        return not self.run.failed

    @property
    def ai_enabled(self) -> bool:
        return self.run.ai_enabled


def assess_sample(
    *,
    limit: int = 20,
    use_cache: bool = True,
    settings: Settings | None = None,
    provider: LLMProvider | None = None,
    repository: Repository | None = None,
    config: ConfigSnapshot | None = None,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> SampleOutcome:
    """Assess a small sample of the register and report what the model said.

    Stores no screening result. The point of the sample is that a person reads
    twenty of them before the scoring stage is built on top.
    """
    resolved = settings or get_settings()
    store = repository or Repository(get_session_factory())

    check = store.check_schema()
    if not check.ok:
        raise DatabaseNotReady(check)

    active = config or configuration_service.snapshot(repository=store)
    rules_config = active.rules
    profile = active.profile
    engine = RuleEngine(rules_config)

    page = store.list_tenders(limit=limit * _CANDIDATE_OVERSAMPLE)
    candidates: list[Candidate] = []
    considered = 0
    for tender in page.items:
        considered += 1
        candidate = Candidate(tender=tender, rules=engine.evaluate(tender))
        if candidate.routed_to_assess:
            candidates.append(candidate)
        if len(candidates) >= limit:
            break

    model = provider or get_provider(resolved)
    cache = ResponseCache.under(resolved.data_dir, enabled=use_cache)

    if not model.enabled:
        # Nothing is called and nothing is spent, so there is no run to record.
        run = assess(
            candidates,
            provider=model,
            profile=profile,
            prompt_version=prompt_version,
            cache=cache,
        )
        return SampleOutcome(
            run=run,
            run_id=None,
            considered=considered,
            rules_version=rules_config.rules_version,
            profile_version=profile.profile_version,
            cache_enabled=use_cache,
        )

    started = store.start_run(RunKind.SCREEN)
    try:
        run = assess(
            candidates,
            provider=model,
            profile=profile,
            prompt_version=prompt_version,
            cache=cache,
            notice_chars=resolved.llm_notice_chars,
            concurrency=resolved.llm_concurrency,
        )
    except Exception as exc:
        store.finish_run(
            started.run_id,
            status=RunStatus.FAILED,
            errors=[str(exc)],
            provider=model.name,
            model=model.model,
            prompt_version=prompt_version,
        )
        raise

    store.finish_run(
        started.run_id,
        status=RunStatus.SUCCESS if not run.failed else RunStatus.PARTIAL,
        counts=run.counts,
        errors=run.errors,
        tokens_in=run.tokens_in,
        tokens_out=run.tokens_out,
        provider=run.provider,
        model=run.model,
        prompt_version=run.prompt_version,
        latency_ms=run.latency_ms,
    )

    return SampleOutcome(
        run=run,
        run_id=started.run_id,
        considered=considered,
        rules_version=rules_config.rules_version,
        profile_version=profile.profile_version,
        cache_enabled=use_cache,
    )


# ----------------------------------------------------- stage 3: score and store


@dataclass(frozen=True)
class Ranked:
    """One row of the register, as the run report shows it."""

    tender_id: str
    title: str
    score: int
    rules_only_score: int | None
    band: Band
    reason_codes: tuple[str, ...]
    domain_rules: tuple[str, ...]
    published_date: date | None

    @property
    def priority(self) -> int:
        """What the register orders on: the rules grade where there is one."""
        return self.rules_only_score if self.rules_only_score is not None else self.score


@dataclass(frozen=True)
class _Screened:
    """One finished notice, with everything the ordering and the counts need."""

    row: Ranked
    decision: Decision


@dataclass(frozen=True)
class ScreenOutcome:
    """One full screening run, in the shape the CLI and the web layer report it."""

    run_id: str | None
    versions: ScreeningVersions
    provider: str
    model: str | None
    ai_enabled: bool
    # How many notices in the register were already current and not re-screened.
    already_current: int
    screened: int
    # Notices whose assessment failed. Stored and visible, and screened again next run.
    to_retry: int
    tokens_in: int
    tokens_out: int
    errors: tuple[str, ...]
    by_score: dict[int, int]
    # The rules grade, 0 upwards, and only where there is one. Counted apart from
    # by_score on purpose: there the 1,498 notices with no evidence sit at 2,
    # beside notices that genuinely matched a domain rule.
    by_rules_only_score: dict[int, int]
    by_band: dict[str, int]
    by_reason_code: dict[str, int]
    top: tuple[Ranked, ...]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def priority_label(self) -> str:
        """What the ordering column is called on screen.

        Never "score" when no model ran: the number is a keyword grade, and a
        colleague reading it as a judgement is exactly the confusion that keeping
        the two in separate fields exists to prevent.
        """
        return "Score" if self.ai_enabled else "Rules priority"


ProgressCallback = Callable[[int, int], None]


def screen_register(
    *,
    limit: int | None = None,
    rescreen: bool = False,
    use_cache: bool = True,
    settings: Settings | None = None,
    provider: LLMProvider | None = None,
    repository: Repository | None = None,
    config: ConfigSnapshot | None = None,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    on_progress: ProgressCallback | None = None,
) -> ScreenOutcome:
    """Screen every notice whose current result no longer reflects how we screen.

    Runs all three stages and stores one screening result per notice. Nothing is
    deleted and nothing is edited: a new result supersedes the previous one, which
    keeps every value it had.

    With no model configured this still does useful work - the rules grade the
    evidence and the register sorts on that grade. Turning a model on later makes
    every rules-only result stale, so the next run re-screens them with no
    rebuilding and no deletion.
    """
    resolved = settings or get_settings()
    store = repository or Repository(get_session_factory())

    check = store.check_schema()
    if not check.ok:
        raise DatabaseNotReady(check)

    # Taken once. Every notice in this run answers to this snapshot, whatever is
    # saved from the settings page while it is going.
    active = config or configuration_service.snapshot(repository=store)
    rules_config = active.rules
    profile = active.profile
    policy = active.policy
    engine = RuleEngine(rules_config)
    model = provider or get_provider(resolved)
    cache = ResponseCache.under(resolved.data_dir, enabled=use_cache)

    versions = active.versions(prompt_version=prompt_version if model.enabled else None)

    total_in_register, stale = _work_to_do(store, versions, model, rescreen=rescreen)
    if limit is not None:
        stale = stale[:limit]

    started = store.start_run(RunKind.SCREEN) if stale else None
    collected: list[_Screened] = []
    errors: list[str] = []
    tokens_in = tokens_out = 0
    ai_enabled = False
    done = 0

    try:
        for start in range(0, len(stale), _BATCH):
            batch = store.get_tenders(stale[start : start + _BATCH])
            candidates = [
                Candidate(tender=tender, rules=engine.evaluate(tender)) for tender in batch
            ]

            run = assess(
                candidates,
                provider=model,
                profile=profile,
                prompt_version=prompt_version,
                cache=cache,
                notice_chars=resolved.llm_notice_chars,
                concurrency=resolved.llm_concurrency,
            )
            ai_enabled = run.ai_enabled
            tokens_in += run.tokens_in
            tokens_out += run.tokens_out
            errors.extend(run.errors)

            outcomes = {item.tender_id: item for item in run.results}
            for candidate in candidates:
                decision = decide(
                    candidate.tender,
                    candidate.rules,
                    policy=policy,
                    outcome=outcomes.get(candidate.tender.id),
                    ai_enabled=run.ai_enabled,
                )
                store.save_screening_result(
                    _as_result(
                        decision,
                        candidate=candidate,
                        versions=versions,
                        provider=model,
                        outcome=outcomes.get(candidate.tender.id),
                    )
                )
                collected.append(_ranked(decision, candidate))

            done += len(batch)
            log.info("screening_progress", screened=done, of=len(stale))
            if on_progress is not None:
                on_progress(done, len(stale))
    except Exception as exc:
        if started is not None:
            store.finish_run(
                started.run_id,
                status=RunStatus.FAILED,
                errors=[str(exc)],
                provider=model.name,
                model=model.model,
                prompt_version=versions.prompt_version,
            )
        raise

    outcome = _screen_outcome(
        collected,
        run_id=started.run_id if started is not None else None,
        versions=versions,
        provider=model,
        ai_enabled=ai_enabled,
        already_current=total_in_register - len(stale),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        errors=errors,
    )

    if started is not None:
        store.finish_run(
            started.run_id,
            status=RunStatus.SUCCESS if not errors else RunStatus.PARTIAL,
            counts={
                "screened": outcome.screened,
                "shortlist": outcome.by_band.get(Band.SHORTLIST.value, 0),
                "review": outcome.by_band.get(Band.REVIEW.value, 0),
                "archive": outcome.by_band.get(Band.ARCHIVE.value, 0),
                "to_retry": outcome.to_retry,
            },
            errors=errors,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            provider=model.name,
            model=model.model,
            prompt_version=versions.prompt_version,
        )

    log.info(
        "screening_finished",
        provider=model.name,
        ai_enabled=ai_enabled,
        screened=outcome.screened,
        to_retry=outcome.to_retry,
        **{f"band_{band}": count for band, count in outcome.by_band.items()},
    )
    return outcome


def _work_to_do(
    store: Repository,
    versions: ScreeningVersions,
    provider: LLMProvider,
    *,
    rescreen: bool,
) -> tuple[int, list[str]]:
    """How many notices the register holds, and which of them need screening."""
    total = store.list_tenders(limit=1).total
    if rescreen:
        return total, _all_tender_ids(store)
    return total, store.ids_needing_screening(versions, provider.name, provider.model)


def _all_tender_ids(store: Repository) -> list[str]:
    ids: list[str] = []
    while True:
        page: TenderPage = store.list_tenders(limit=MAX_PAGE_SIZE, offset=len(ids))
        ids.extend(tender.id for tender in page.items)
        if len(ids) >= page.total or not page.items:
            return ids


def _as_result(
    decision: Decision,
    *,
    candidate: Candidate,
    versions: ScreeningVersions,
    provider: LLMProvider,
    outcome: AssessmentOutcome | None,
) -> ScreeningResult:
    """The decision plus the facts only the service knows: who judged it, under what."""
    return ScreeningResult(
        tender_id=decision.tender_id,
        score=decision.score,
        band=decision.band,
        rules_only_score=decision.rules_only_score,
        confidence=decision.confidence,
        confidence_reasons=decision.confidence_reasons,
        reason_codes=decision.reason_codes,
        domain_strength_rank=decision.domain_strength_rank,
        domain_rules_matched=decision.domain_rules_matched,
        rules=candidate.rules,
        assessment=outcome.assessment if outcome is not None else None,
        explanation=decision.explanation,
        ai_enabled=decision.ai_enabled,
        screened_content_hash=candidate.tender.content_hash,
        provider=provider.name,
        model=provider.model,
        prompt_version=versions.prompt_version,
        rules_version=versions.rules_version,
        policy_version=versions.policy_version,
        profile_version=versions.profile_version,
    )


def _ranked(decision: Decision, candidate: Candidate) -> _Screened:
    evidence = RulesEvidence.of(candidate.rules)
    return _Screened(
        row=Ranked(
            tender_id=decision.tender_id,
            title=_title(candidate.tender),
            score=decision.score,
            rules_only_score=decision.rules_only_score,
            band=decision.band,
            reason_codes=tuple(decision.reason_codes),
            domain_rules=evidence.domain_rules,
            published_date=candidate.tender.published_date,
        ),
        decision=decision,
    )


def _screen_outcome(
    collected: list[_Screened],
    *,
    run_id: str | None,
    versions: ScreeningVersions,
    provider: LLMProvider,
    ai_enabled: bool,
    already_current: int,
    tokens_in: int,
    tokens_out: int,
    errors: list[str],
) -> ScreenOutcome:
    by_score: dict[int, int] = {}
    by_grade: dict[int, int] = {}
    by_band: dict[str, int] = {}
    by_reason: dict[str, int] = {}

    for item in collected:
        row = item.row
        by_score[row.score] = by_score.get(row.score, 0) + 1
        by_band[row.band.value] = by_band.get(row.band.value, 0) + 1
        if row.rules_only_score is not None:
            by_grade[row.rules_only_score] = by_grade.get(row.rules_only_score, 0) + 1
        for code in row.reason_codes:
            by_reason[code] = by_reason.get(code, 0) + 1

    return ScreenOutcome(
        run_id=run_id,
        versions=versions,
        provider=provider.name,
        model=provider.model,
        ai_enabled=ai_enabled,
        already_current=max(0, already_current),
        screened=len(collected),
        to_retry=sum(1 for item in collected if item.decision.retry_next_run),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        errors=tuple(errors),
        by_score=dict(sorted(by_score.items(), reverse=True)),
        by_rules_only_score=dict(sorted(by_grade.items(), reverse=True)),
        by_band=by_band,
        by_reason_code=dict(sorted(by_reason.items(), key=lambda item: (-item[1], item[0]))),
        top=_top_rows(collected),
    )


def _top_rows(collected: list[_Screened]) -> tuple[Ranked, ...]:
    """The head of the register, in the order the register puts them in.

    Ordered by ``ranking_key`` so there is exactly one definition of that order.
    Sorted by id first, so notices that tie are still in a fixed sequence.
    """
    ordered = sorted(collected, key=lambda item: item.row.tender_id)
    ordered.sort(
        key=lambda item: ranking_key(item.decision, item.row.published_date),
        reverse=True,
    )
    return tuple(item.row for item in ordered[:TOP_ROWS])


def _title(tender: Tender) -> str:
    """The buyer's own words where we have them, never TED's composed title."""
    return tender.title_native or tender.title
