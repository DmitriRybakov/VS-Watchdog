"""Running the pipeline and looking at what it did, both from the browser.

The three endpoints that start work all do the same thing: take the lock, hand
the work to a background task, and return immediately with the header re-rendered
so the buttons are already disabled. The page then polls :func:`status` while the
run is going. Nothing here waits for a run to finish - a request that did would
time out long before an ingest did.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse

from watchdog import __version__
from watchdog.core.enums import RunKind
from watchdog.core.settings import Settings
from watchdog.services import jobs
from watchdog.services import query as query_service
from watchdog.storage.repository import Repository
from watchdog.web.deps import Config, Store
from watchdog.web.templating import templates

router = APIRouter(tags=["runs"])


@router.get("/runs", response_class=HTMLResponse)
def runs(request: Request, store: Store, settings: Config) -> HTMLResponse:
    """Recent runs with their window, counts, duration, errors and status."""
    return templates.TemplateResponse(
        request,
        "runs.html",
        {
            "runs": store.list_runs(limit=40),
            "job": store.get_job(query_service.PIPELINE_JOB),
            "last_ingest": store.latest_run(RunKind.INGEST),
            "last_screen": store.latest_run(RunKind.SCREEN),
            "quarantined": store.list_quarantine(unresolved_only=True, limit=20),
            "ai_enabled": query_service.ai_configured(settings),
            "busy_error": None,
            "finished": False,
            "version": __version__,
        },
    )


@router.post("/runs/update", response_class=HTMLResponse)
def update(
    request: Request, background: BackgroundTasks, store: Store, settings: Config
) -> HTMLResponse:
    """Start an ingest-and-screen run."""
    return _start(jobs.UPDATE, request, background, store, settings)


@router.post("/runs/rescreen", response_class=HTMLResponse)
def rescreen(
    request: Request, background: BackgroundTasks, store: Store, settings: Config
) -> HTMLResponse:
    """Re-run screening over every notice, whatever its versions say."""
    return _start(jobs.RESCREEN, request, background, store, settings)


@router.get("/runs/status", response_class=HTMLResponse)
def status(request: Request, store: Store, settings: Config) -> HTMLResponse:
    """The header belt. Polled by HTMX while a run is going, and once after.

    Reads one row. It must stay this cheap: it is requested every second by every
    open tab for as long as a run lasts.
    """
    return _belt(request, store, settings)


# ------------------------------------------------------------------- internals


def _start(
    job: str,
    request: Request,
    background: BackgroundTasks,
    store: Repository,
    settings: Settings,
) -> HTMLResponse:
    error: str | None = None
    try:
        jobs.claim(job, repository=store)
        # Queued only after the lock is held, so a refused press starts nothing.
        background.add_task(jobs.run, job, repository=store, settings=settings)
    except jobs.JobBusy as exc:
        error = str(exc)

    return _belt(request, store, settings, error=error)


def _belt(
    request: Request,
    store: Repository,
    settings: Settings,
    *,
    error: str | None = None,
) -> HTMLResponse:
    job = store.get_job(query_service.PIPELINE_JOB)

    return templates.TemplateResponse(
        request,
        "partials/status.html",
        {
            "job": job,
            "last_ingest": store.latest_run(RunKind.INGEST),
            "last_screen": store.latest_run(RunKind.SCREEN),
            "ai_enabled": query_service.ai_configured(settings),
            "busy_error": error,
            # True when a run has stopped and left a result behind. The template
            # uses it to refresh the results once, rather than on every poll.
            "finished": job is not None and not job.held and job.status is not None,
        },
    )
