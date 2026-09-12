"""Reading a source into the register. The only code path that stores a tender.

Three properties this is arranged to keep, because losing any one of them loses a
notice quietly.

**Idempotent.** Identity is (source, source_id), so running the same window twice
updates the same rows. A notice that comes back after a correction is an update,
never a second row and never a lost history.

**Resumable.** The watermark moves only when the source was read to the end and
every write succeeded. A crash therefore leaves it where it was, and the next run
repeats the same window - which is safe precisely because the run is idempotent.

**Loud about what it could not do.** A notice that cannot be mapped is
quarantined by publication number on the run and the rest of the page still
lands. It never stops the run, and it never disappears.

The overlap window exists because sources publish corrections and late entries: a
notice can appear carrying a publication date that a finished run already passed.
Whole days are what TED's query language can express, so the overlap is rounded
down to a date when the query is built; see sources/ted/query.py.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from watchdog.core.clock import utc_now
from watchdog.core.enums import RunKind, RunStatus, SourcePlatform
from watchdog.core.logging import get_logger
from watchdog.core.models import Run, Tender, Watermark
from watchdog.services.ted import get_config
from watchdog.sources.base import TenderSource
from watchdog.sources.errors import MappingError
from watchdog.sources.ted import TedClient, TedSource, TedSourceConfig
from watchdog.storage.db import get_session_factory
from watchdog.storage.repository import Repository

log = get_logger(__name__)

# Long enough that corrections and late entries are caught, short enough that a
# daily run stays small. Overridden per source by its own configuration.
DEFAULT_OVERLAP_HOURS = 48

# How many notices are held before they are written. Small enough that a crash
# loses little work, large enough that the database is not written to per notice.
DEFAULT_BATCH_SIZE = 100


class DatabaseNotReady(RuntimeError):
    """The database has no tables yet, because the migration has not been run.

    Its own type so the caller can say what to do next instead of showing a
    database error to a colleague who has never seen one.
    """


@dataclass(frozen=True)
class IngestWindow:
    """The window one run reads, in UTC, and in plain words why it starts there."""

    window_from: datetime
    window_to: datetime
    reason: str

    @property
    def query_dates(self) -> tuple[date, date]:
        """The window as calendar dates, which is the precision a source query has."""
        return (self.window_from.date(), self.window_to.date())


@dataclass
class IngestCounts:
    """What one run did. Every fetched notice ends up in exactly one of these."""

    fetched: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    quarantined: int = 0
    failed: int = 0

    @property
    def stored(self) -> int:
        return self.new + self.updated + self.unchanged

    @property
    def accounted_for(self) -> int:
        return self.stored + self.quarantined + self.failed

    @property
    def reconciles(self) -> bool:
        return self.fetched == self.accounted_for

    def as_dict(self) -> dict[str, int]:
        return {
            "fetched": self.fetched,
            "new": self.new,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "quarantined": self.quarantined,
            "failed": self.failed,
        }


@dataclass(frozen=True)
class QuarantinedNotice:
    """One notice that could not be mapped, named so a person can go and look."""

    source_id: str
    reason: str


@dataclass(frozen=True)
class IngestOutcome:
    """The result of one run, in the shape the CLI and the web layer report it."""

    status: RunStatus
    window: IngestWindow
    counts: IngestCounts
    quarantined: tuple[QuarantinedNotice, ...] = ()
    errors: tuple[str, ...] = ()
    watermark_advanced: bool = False
    # None only for a dry run, which stores nothing at all, not even the run.
    run_id: str | None = None
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return self.status is RunStatus.SUCCESS


ProgressCallback = Callable[[IngestCounts], None]
SourceFactory = Callable[[str], TenderSource]


def plan_window(
    watermark: Watermark | None,
    *,
    now: datetime,
    overlap_hours: int = DEFAULT_OVERLAP_HOURS,
    backfill_days: int = 30,
    since_days: int | None = None,
    backfill_from: date | None = None,
) -> IngestWindow:
    """Where this run starts and why. Pure: no database, no clock, no network.

    The order is deliberate. An explicit instruction from a person wins over the
    watermark, and the watermark wins over the backfill default. Whatever wins,
    the window never ends later than ``now``.
    """
    if backfill_from is not None:
        window_from = datetime.combine(backfill_from, datetime.min.time(), tzinfo=UTC)
        reason = f"a full backfill from {backfill_from.isoformat()}"
    elif since_days is not None:
        window_from = now - timedelta(days=since_days)
        reason = f"{since_days} day(s) back from now, as asked"
    elif watermark is None or watermark.last_successful_at is None:
        window_from = now - timedelta(days=backfill_days)
        reason = f"the first run for this source, reaching {backfill_days} days back"
    else:
        window_from = watermark.last_successful_at - timedelta(hours=overlap_hours)
        reason = (
            f"the last successful run at "
            f"{watermark.last_successful_at.strftime('%Y-%m-%d %H:%M')} UTC, "
            f"less a {overlap_hours}-hour overlap"
        )

    if window_from > now:
        raise ValueError(
            f"the window would start at {window_from.isoformat()}, which is in the future; "
            "check the date you gave"
        )

    return IngestWindow(window_from=window_from, window_to=now, reason=reason)


def ingest(
    repository: Repository,
    platform: SourcePlatform,
    make_source: SourceFactory,
    *,
    window: IngestWindow,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
    on_progress: ProgressCallback | None = None,
) -> IngestOutcome:
    """Read one source over one window and store what it returns.

    ``make_source`` is given the run id and returns the adapter, so that every log
    line the source writes carries the run it belongs to. The run therefore exists
    before the first request, which is also what makes a crashed run visible.
    """
    counts = IngestCounts()
    errors: list[str] = []
    quarantined: list[QuarantinedNotice] = []

    run_id = _start_run(repository, platform, window, dry_run=dry_run)
    source = make_source(run_id)

    log.info(
        "ingest_started",
        run_id=run_id,
        source=platform.value,
        window_from=window.window_from.isoformat(),
        window_to=window.window_to.isoformat(),
        dry_run=dry_run,
    )

    complete = True
    batch: list[Tender] = []
    window_from, window_to = window.query_dates

    try:
        for raw in source.discover(window_from, window_to):
            counts.fetched += 1

            try:
                tender = source.to_tender(raw)
            except MappingError as exc:
                item = QuarantinedNotice(
                    source_id=exc.source_id or raw.source_id,
                    reason=str(exc),
                )
                quarantined.append(item)
                errors.append(f"quarantined {item.source_id}: {item.reason}")
                counts.quarantined += 1
                log.warning(
                    "ingest_notice_quarantined",
                    run_id=run_id,
                    source=platform.value,
                    source_id=item.source_id,
                    reason=item.reason,
                )
                continue

            batch.append(tender)
            if len(batch) >= batch_size:
                written = _write(repository, batch, run_id=run_id, dry_run=dry_run)
                batch = []
                complete = _apply(written, counts, errors, run_id, platform) and complete
                if on_progress is not None:
                    on_progress(counts)
                if not complete:
                    break
    except Exception as exc:
        # Anything the source raises: a transport failure, an incomplete page, a
        # rejected query. The window is not finished, so the watermark stays.
        complete = False
        errors.append(f"the source could not be read to the end: {exc}")
        log.error(
            "ingest_source_failed",
            run_id=run_id,
            source=platform.value,
            reason=str(exc),
            exc_info=True,
        )

    # Whatever arrived before the failure is still worth keeping; a partial run
    # that threw its work away would have to fetch it all again.
    if batch:
        written = _write(repository, batch, run_id=run_id, dry_run=dry_run)
        complete = _apply(written, counts, errors, run_id, platform) and complete
        if on_progress is not None:
            on_progress(counts)

    if not counts.reconciles:
        # An accounting mistake means we do not know what happened to a notice,
        # which is exactly when the watermark must not move.
        complete = False
        errors.append(
            f"the counts do not add up: {counts.fetched} fetched but "
            f"{counts.accounted_for} accounted for"
        )
        log.error("ingest_counts_do_not_reconcile", run_id=run_id, **counts.as_dict())

    status = _status(complete, counts)
    advanced = complete and not dry_run
    if advanced:
        repository.set_watermark(platform, window.window_to)

    if not dry_run:
        repository.finish_run(
            run_id,
            status=status,
            counts=counts.as_dict(),
            errors=errors,
            watermark_advanced=advanced,
        )

    log.info(
        "ingest_finished",
        run_id=run_id,
        source=platform.value,
        status=status.value,
        watermark_advanced=advanced,
        dry_run=dry_run,
        **counts.as_dict(),
    )

    return IngestOutcome(
        status=status,
        window=window,
        counts=counts,
        quarantined=tuple(quarantined),
        errors=tuple(errors),
        watermark_advanced=advanced,
        run_id=None if dry_run else run_id,
        dry_run=dry_run,
    )


def ingest_ted(
    *,
    repository: Repository | None = None,
    config: TedSourceConfig | None = None,
    client: TedClient | None = None,
    since_days: int | None = None,
    backfill_from: date | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
    on_progress: ProgressCallback | None = None,
) -> IngestOutcome:
    """Read TED into the register. The configuration seed is read here, not deeper."""
    store = _ready(repository or Repository(get_session_factory()))
    ted_config = config or get_config()

    window = plan_window(
        store.get_watermark(SourcePlatform.TED),
        now=now or utc_now(),
        overlap_hours=ted_config.overlap_days * 24,
        backfill_days=ted_config.backfill_days,
        since_days=since_days,
        backfill_from=backfill_from,
    )

    owned = client is None
    ted = client or TedClient()
    try:
        return ingest(
            store,
            SourcePlatform.TED,
            lambda run_id: TedSource(ted, ted_config, run_id=run_id),
            window=window,
            dry_run=dry_run,
            on_progress=on_progress,
        )
    finally:
        if owned:
            ted.close()


def recent_runs(*, limit: int = 20, repository: Repository | None = None) -> list[Run]:
    """The most recent runs of any kind, newest first, for `watchdog runs`."""
    store = _ready(repository or Repository(get_session_factory()))
    return store.list_runs(limit=limit)


# ------------------------------------------------------------------- internals


def _ready(store: Repository) -> Repository:
    if not store.is_ready():
        raise DatabaseNotReady("the database has no tables yet")
    return store


@dataclass
class _Written:
    """What one batch write did, or why it did nothing."""

    new: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0
    error: str | None = None
    error_type: str | None = None


def _start_run(
    repository: Repository,
    platform: SourcePlatform,
    window: IngestWindow,
    *,
    dry_run: bool,
) -> str:
    """The run id everything below is logged and recorded against.

    A dry run gets one too, so its log lines can be followed, but no row: a dry
    run must leave the database exactly as it found it.
    """
    if dry_run:
        return uuid.uuid4().hex

    run = repository.start_run(
        RunKind.INGEST,
        source=platform,
        window_from=window.window_from,
        window_to=window.window_to,
    )
    return run.run_id


def _write(
    repository: Repository,
    batch: Sequence[Tender],
    *,
    run_id: str,
    dry_run: bool,
) -> _Written:
    try:
        if dry_run:
            known = repository.known_tender_ids(tender.id for tender in batch)
            new = sum(1 for tender in batch if tender.id not in known)
            return _Written(new=new, updated=len(batch) - new)

        stats = repository.upsert_tenders(batch, run_id=run_id)
        return _Written(new=stats.new, updated=stats.updated, unchanged=stats.unchanged)
    except Exception as exc:
        return _Written(
            failed=len(batch),
            error=f"{len(batch)} notice(s) could not be written: {exc}",
            error_type=type(exc).__name__,
        )


def _apply(
    written: _Written,
    counts: IngestCounts,
    errors: list[str],
    run_id: str,
    platform: SourcePlatform,
) -> bool:
    """Fold one batch result into the counts. False means stop: writing is broken."""
    counts.new += written.new
    counts.updated += written.updated
    counts.unchanged += written.unchanged
    counts.failed += written.failed

    if written.error is None:
        return True

    errors.append(written.error)
    log.error(
        "ingest_batch_write_failed",
        run_id=run_id,
        source=platform.value,
        notices=written.failed,
        reason=written.error,
        error_type=written.error_type,
    )
    return False


def _status(complete: bool, counts: IngestCounts) -> RunStatus:
    """Complete means the whole window was read and written.

    A quarantined notice does not make a run incomplete. It will be just as
    unmappable tomorrow, so holding the watermark for it would pin the window open
    for ever; it is recorded by name on the run instead.
    """
    if complete:
        return RunStatus.SUCCESS
    return RunStatus.PARTIAL if counts.stored else RunStatus.FAILED


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_OVERLAP_HOURS",
    "DatabaseNotReady",
    "IngestCounts",
    "IngestOutcome",
    "IngestWindow",
    "QuarantinedNotice",
    "ingest",
    "ingest_ted",
    "plan_window",
    "recent_runs",
]
