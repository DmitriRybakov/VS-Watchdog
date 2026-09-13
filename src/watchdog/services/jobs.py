"""Running the pipeline from the browser, so nobody needs a terminal.

The mechanism is deliberately the smallest one that works: a background task in
the web process, and one lock row in the database. No Celery, no Redis, no queue.
This is a **triggered job**, not a scheduler - the scheduler still lives outside
the web process and calls the CLI.

What the lock row buys, beyond stopping two runs at once:

- The page can show what is happening without the web process holding any state,
  so a reload mid-run shows the run rather than an idle header.
- A run that died with the process is visible as a stranded lock at startup and
  is cleared once, by :func:`recover`, rather than leaving the buttons disabled
  forever with nothing to press.

Failures are caught and written down as one sentence. The person who pressed the
button sees that sentence and a Retry button; the stack trace goes to the log.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from watchdog.core.enums import RunKind, RunStatus
from watchdog.core.logging import get_logger
from watchdog.core.models import JobState
from watchdog.core.settings import Settings, get_settings
from watchdog.services.ingest import DatabaseNotReady, ingest_ted
from watchdog.services.query import PIPELINE_JOB
from watchdog.services.screen import screen_register
from watchdog.storage.db import get_session_factory
from watchdog.storage.errors import describe_error
from watchdog.storage.repository import Repository

log = get_logger(__name__)

UPDATE = "update"
RESCREEN = "rescreen"
# The same two jobs, started from a terminal instead of a button. They take the
# same lock: during a deployment one of these runs for an hour against the hosted
# database while the site is live, and that is exactly when somebody presses
# Update. Two runs over one register survive it - a notice keeps its row - but
# each would report counts for the other's work, and numbers nobody can account
# for are numbers nobody trusts.
CLI_INGEST = "cli-ingest"
CLI_SCREEN = "cli-screen"

JOB_LABELS = {
    UPDATE: "Update from TED",
    RESCREEN: "Re-screen",
    CLI_INGEST: "Ingest (command line)",
    CLI_SCREEN: "Re-screen (command line)",
}

JOB_KINDS = {
    UPDATE: RunKind.INGEST,
    RESCREEN: RunKind.SCREEN,
    CLI_INGEST: RunKind.INGEST,
    CLI_SCREEN: RunKind.SCREEN,
}

# job_lock.phase is a varchar(64) and PostgreSQL enforces that. A phase too long
# would fail mid-run, on the progress report rather than on the work.
PHASE_LENGTH = 64


class JobBusy(RuntimeError):
    """Something is already running. Raised instead of starting a second run."""


@dataclass(frozen=True)
class JobStarted:
    """What the route tells the page after a successful press."""

    job: str
    label: str
    state: JobState


def status(*, repository: Repository | None = None) -> JobState | None:
    """What the header polls. One row, one read, no work."""
    store = repository or Repository(get_session_factory())
    return store.get_job(PIPELINE_JOB)


def recover(*, repository: Repository | None = None) -> list[str]:
    """Clear anything left RUNNING by a process that died. Called once at startup."""
    store = repository or Repository(get_session_factory())
    stranded = store.recover_interrupted_runs()
    if stranded:
        log.warning("runs_marked_interrupted", run_ids=stranded, count=len(stranded))
    return stranded


def claim(job: str, *, repository: Repository | None = None) -> JobStarted:
    """Take the lock for a job, or refuse because one is already going.

    Separate from :func:`run` because a route has to know whether the press was
    accepted *before* it returns a page, while the work itself happens after.
    """
    if job not in JOB_LABELS:
        raise ValueError(f"unknown job {job!r}; the jobs are: {', '.join(sorted(JOB_LABELS))}")

    store = repository or Repository(get_session_factory())
    state = store.acquire_job(PIPELINE_JOB, kind=JOB_KINDS[job])

    if state is None:
        raise JobBusy(_busy(store.get_job(PIPELINE_JOB)))

    store.update_job(PIPELINE_JOB, phase=phase(job, "starting"))
    log.info("job_claimed", job=job)
    return JobStarted(job=job, label=JOB_LABELS[job], state=state)


def phase(job: str, step: str) -> str:
    """What a job reports it is doing, always beginning with which job it is.

    The label is part of the phase rather than a column of its own, so whoever is
    refused the lock is told what holds it, whichever interface they are in front
    of and whichever one is holding it.
    """
    return f"{JOB_LABELS[job]}: {step}"[:PHASE_LENGTH]


class Held:
    """The lock while a command holds it, and where that command reports progress.

    Every report moves ``updated_at``, which is the heartbeat the takeover rule
    reads: a command that is working is never mistaken for one that died, and a
    command that was killed stops looking alive a quarter of an hour later.
    """

    def __init__(self, store: Repository, job: str) -> None:
        self._store = store
        self._job = job
        self.counts: dict[str, int] = {}
        self.status = RunStatus.SUCCESS

    def report(
        self,
        step: str,
        *,
        processed: int | None = None,
        total: int | None = None,
        counts: dict[str, int] | None = None,
        run_id: str | None = None,
    ) -> None:
        self._store.update_job(
            PIPELINE_JOB,
            phase=phase(self._job, step),
            processed=processed,
            total=total,
            counts=counts,
            run_id=run_id,
        )

    def finished(self, *, counts: dict[str, int], status: RunStatus) -> None:
        """What to leave behind in the lock row for the page to show afterwards."""
        self.counts = dict(counts)
        self.status = status


@contextmanager
def hold(job: str, *, repository: Repository | None = None) -> Iterator[Held]:
    """Hold the pipeline lock for the duration of a command, then release it.

    The same lock the buttons take, so a terminal and a browser cannot both be
    running the pipeline. Released whichever way the command ends, including when
    somebody presses Ctrl-C: a command that is gone should not leave a lock behind
    for fifteen minutes when it knows perfectly well that it is stopping.
    """
    store = repository or Repository(get_session_factory())

    # Before the lock, because a database with no tables cannot record one, and
    # "run the migration" is a better answer than a failed INSERT on job_lock.
    check = store.check_schema()
    if not check.ok:
        raise DatabaseNotReady(check)

    claim(job, repository=store)
    held = Held(store, job)

    try:
        yield held
    except KeyboardInterrupt:
        store.release_job(
            PIPELINE_JOB,
            status=RunStatus.INTERRUPTED,
            counts=held.counts,
            error="The command was stopped before it finished.",
        )
        log.warning("job_stopped", job=job)
        raise
    except Exception as exc:
        store.release_job(
            PIPELINE_JOB, status=RunStatus.FAILED, counts=held.counts, error=_brief(exc)
        )
        log.warning("job_error", job=job, error=_brief(exc))
        raise
    else:
        store.release_job(PIPELINE_JOB, status=held.status, counts=held.counts)
        log.info("job_finished", job=job, **held.counts)


def _busy(state: JobState | None) -> str:
    """Why the press was refused, naming what holds the lock and since when.

    Both halves matter to the person reading it. Which job it is tells them
    whether it is theirs - a colleague's button or a long backfill running from
    somebody's terminal - and when it last reported tells them whether waiting is
    worth it.
    """
    if state is None or state.phase is None:
        return "A run is already in progress. Wait for it to finish, then try again."

    started = f", started at {state.started_at:%H:%M} UTC" if state.started_at else ""
    return (
        f'A run is already in progress: "{state.phase}"{started}, '
        f"last reported at {state.updated_at:%H:%M} UTC. "
        "Wait for it to finish, then try again."
    )


def run(
    job: str,
    *,
    repository: Repository | None = None,
    settings: Settings | None = None,
) -> None:
    """Do the work, reporting progress into the lock row. Never raises.

    A background task has nobody to raise to: an exception here would be swallowed
    by the server and the lock would stay held. So everything is caught, written
    down in one sentence and the lock is released either way.
    """
    store = repository or Repository(get_session_factory())
    resolved = settings or get_settings()
    counts: dict[str, int] = {}

    try:
        if job == UPDATE:
            counts = _update(store, resolved)
        else:
            counts = _rescreen(store, resolved, job=job, rescreen=True)
    except DatabaseNotReady as exc:
        _fail(store, job, message=str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - a background task has nobody to raise to
        log.exception("job_failed", job=job)
        _fail(store, job, message=_brief(exc))
        return

    status_value = RunStatus.PARTIAL if counts.get("errors") else RunStatus.SUCCESS
    store.release_job(PIPELINE_JOB, status=status_value, counts=counts)
    log.info("job_finished", job=job, **counts)


# ------------------------------------------------------------------- internals


def _update(store: Repository, settings: Settings) -> dict[str, int]:
    """Read TED, then screen whatever the read made stale. One press, both stages."""
    reading = phase(UPDATE, "reading notices from TED")
    store.update_job(PIPELINE_JOB, phase=reading, processed=0, total=0)

    outcome = ingest_ted(
        repository=store,
        # The ingest reports its running counts, not a fraction: it does not know
        # how many notices the window holds until it has read them all.
        on_progress=lambda counts: store.update_job(
            PIPELINE_JOB,
            phase=reading,
            processed=counts.stored,
            total=counts.fetched,
            counts=counts.as_dict(),
        ),
    )
    if outcome.run_id is not None:
        store.update_job(PIPELINE_JOB, run_id=outcome.run_id)

    screened = _rescreen(store, settings, job=UPDATE, rescreen=False)

    return {
        "new": outcome.counts.new,
        "updated": outcome.counts.updated,
        "unchanged": outcome.counts.unchanged,
        "quarantined": outcome.counts.quarantined,
        "screened": screened.get("screened", 0),
        "errors": len(outcome.errors) + screened.get("errors", 0),
    }


def _rescreen(store: Repository, settings: Settings, *, job: str, rescreen: bool) -> dict[str, int]:
    step = "re-screening every notice" if rescreen else "screening new and changed notices"
    reporting = phase(job, step)
    store.update_job(PIPELINE_JOB, phase=reporting, processed=0, total=0)

    outcome = screen_register(
        repository=store,
        settings=settings,
        rescreen=rescreen,
        on_progress=lambda done, total: store.update_job(
            PIPELINE_JOB, phase=reporting, processed=done, total=total
        ),
    )
    return {
        "screened": outcome.screened,
        "shortlist": outcome.by_band.get("shortlist", 0),
        "review": outcome.by_band.get("review", 0),
        "archive": outcome.by_band.get("archive", 0),
        "to_retry": outcome.to_retry,
        "errors": len(outcome.errors),
    }


def _fail(store: Repository, job: str, *, message: str) -> None:
    store.release_job(PIPELINE_JOB, status=RunStatus.FAILED, error=message)
    log.warning("job_error", job=job, error=message)


def _brief(exc: Exception) -> str:
    """One sentence for a person, not a stack trace and not a repr."""
    text = describe_error(exc)
    return text if len(text) <= 300 else f"{text[:297]}..."
