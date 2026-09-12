"""Screening operations the CLI and the web layer can call.

Stage 2 is the model assessment. It produces the three axis judgements with their
quotes; it does **not** produce a score, a band or a stored screening result,
because the scoring policy is stage 3 and ``docs/SCREENING_POLICY.md`` is still a
stub. Nothing about a tender is written here - the run is recorded so the cost and
the versions can be traced, and that is all.

The configuration seeds are read here, in the service layer, because business
logic must not read a config file directly. When the config_version table becomes
the active source, only this module changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from watchdog.core.enums import RunKind, RunStatus
from watchdog.core.logging import get_logger
from watchdog.core.settings import Settings, get_settings
from watchdog.llm.cache import ResponseCache
from watchdog.llm.provider import LLMError, LLMProvider, get_provider
from watchdog.screening.assess import AssessmentOutcome, AssessRun, Candidate, assess
from watchdog.screening.config import DEFAULT_CONFIG_PATH as RULES_CONFIG_PATH
from watchdog.screening.config import load_rules_config
from watchdog.screening.profile import DEFAULT_PROFILE_PATH, load_profile
from watchdog.screening.prompts import DEFAULT_PROMPT_VERSION
from watchdog.screening.rules import RuleEngine
from watchdog.services.ingest import DatabaseNotReady
from watchdog.storage.db import get_session_factory
from watchdog.storage.repository import Repository

log = get_logger(__name__)

# Re-exported so cli.py and the web layer can name the failure without importing
# the llm package, which they are not allowed to do.
__all__ = ["AssessmentOutcome", "LLMError", "SampleOutcome", "assess_sample"]

# How many notices to read from the register for each one we hope to assess. The
# rules stage routes some away, so asking for exactly the sample size would
# usually return fewer than asked for.
_CANDIDATE_OVERSAMPLE = 4


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
    rules_path: Path | str = RULES_CONFIG_PATH,
    profile_path: Path | str = DEFAULT_PROFILE_PATH,
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

    rules_config = load_rules_config(rules_path)
    profile = load_profile(profile_path)
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
