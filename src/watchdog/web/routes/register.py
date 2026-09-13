"""The register: the triage queue, the table, the detail page and the exports.

Every handler here does the same three things and nothing else: read the query
string, call one service function, render. There is no query, no filtering and no
scoring in this file, and none in the templates it renders.

Two behaviours worth naming, because they are the ones that make the page usable:

- **Recording a verdict does not navigate.** The verdict POST re-renders the
  results region with the cursor already moved on, so ``y``, ``n`` and ``u`` walk
  a queue without a page load and without the list jumping under the pointer.
- **The filter state lives in the URL.** Every HTMX request pushes it, so any view
  a colleague reaches can be bookmarked, shared, and opened again tomorrow.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from watchdog.core.enums import Verdict
from watchdog.core.models import RegisterRow
from watchdog.core.settings import Settings
from watchdog.services import export as export_service
from watchdog.services import query as query_service
from watchdog.services.query import FilterError, RegisterView
from watchdog.services.review import UnattributedReview, UnknownTender, record_verdict
from watchdog.storage.repository import Repository
from watchdog.web import reviewer as reviewer_cookie
from watchdog.web.deps import Config, Store
from watchdog.web.reviewer import Reviewer
from watchdog.web.templating import templates

router = APIRouter(tags=["register"])

# Which template renders the swappable region, per mode. The page and every
# partial response go through the same one, so what HTMX swaps in is always what
# a full page load would have produced.
_RESULTS = {"triage": "partials/triage.html", "table": "partials/table.html"}


@router.get("/", response_class=HTMLResponse)
@router.get("/register", response_class=HTMLResponse)
def register(request: Request, store: Store, settings: Config) -> HTMLResponse:
    """The whole page, or just the results region when HTMX asks for it."""
    try:
        view = query_service.register_view(_params(request), repository=store, settings=settings)
    except FilterError as exc:
        return _filter_problem(request, exc)

    if _is_partial(request):
        return _results(request, view)

    return templates.TemplateResponse(request, "register.html", _context(request, view))


@router.get("/register/results", response_class=HTMLResponse)
def results(request: Request, store: Store, settings: Config) -> HTMLResponse:
    """Just the table or the queue. What filtering, sorting and paging swap."""
    try:
        view = query_service.register_view(_params(request), repository=store, settings=settings)
    except FilterError as exc:
        return _filter_problem(request, exc)
    return _results(request, view)


@router.post("/register/reviewer", response_class=HTMLResponse)
def set_reviewer(request: Request, name: str = Form(default="")) -> HTMLResponse:
    """Remember who is recording decisions in this browser.

    Attribution, not a sign-in: nothing is verified and nothing is authorised by
    it. It exists so that the reviews collected before authentication exists can
    still be told apart afterwards.
    """
    try:
        cleaned = reviewer_cookie.clean(name)
    except reviewer_cookie.InvalidName as exc:
        return _reviewer_panel(request, None, message=str(exc), status_code=400)

    response = _reviewer_panel(request, cleaned)
    if cleaned is None:
        reviewer_cookie.forget(response)
    else:
        reviewer_cookie.remember(response, cleaned)
    return response


@router.post("/register/{tender_id:path}/verdict", response_class=HTMLResponse)
def record(
    request: Request,
    tender_id: str,
    store: Store,
    settings: Config,
    reviewer: Reviewer,
    verdict: str = Form(...),
) -> HTMLResponse:
    """Record a decision and hand back the results region, cursor moved on.

    The cursor advances by one normally. It does not advance while "undecided
    only" is on, because there the decided row leaves the set and the next one
    takes its index - advancing as well would skip a notice, which is the one
    mistake a queue must not make.
    """
    try:
        chosen = Verdict(verdict)
    except ValueError:
        return _problem(
            request,
            f"{verdict!r} is not a verdict. The choices are: "
            f"{', '.join(member.value for member in Verdict)}.",
            status_code=400,
        )

    try:
        record_verdict(tender_id, chosen, reviewed_by=reviewer or "", repository=store)
    except UnattributedReview as exc:
        return _problem(request, str(exc), status_code=400)
    except UnknownTender as exc:
        return _problem(request, str(exc), status_code=404)

    try:
        parsed = query_service.parse(
            _params(request), ai_enabled=query_service.ai_configured(settings)
        )
    except FilterError as exc:
        return _filter_problem(request, exc)

    advance = 0 if parsed.filters.undecided_only else 1
    moved = replace(parsed, cursor=parsed.cursor + advance)
    view = query_service.build(
        moved,
        repository=store,
        ai_enabled=parsed.ai_enabled,
        as_of=parsed.filters.as_of,
    )
    return _results(request, view, reviewer=reviewer)


@router.get("/register/notice/{tender_id:path}", response_class=HTMLResponse)
def detail(request: Request, tender_id: str, store: Store, reviewer: Reviewer) -> HTMLResponse:
    """Everything about one notice, including what the card deliberately leaves out."""
    row = store.get_register_row(tender_id)
    if row is None:
        return _problem(
            request,
            f"The register holds no notice with the id {tender_id}.",
            status_code=404,
        )

    return templates.TemplateResponse(
        request,
        "detail.html",
        {
            "row": row,
            "history": store.screening_history(tender_id),
            "reviews": store.list_reviews(tender_id),
            "changes": store.list_changes(tender_id),
            "back": request.url.query,
            "reviewer": reviewer,
            "version": _version(),
        },
    )


@router.get("/register/export.csv")
def export_csv(request: Request, store: Store, settings: Config) -> Any:
    """Exactly the current filter, as CSV, streamed. Never written to disk."""
    rows = _export_rows(request, store, settings)
    return StreamingResponse(
        export_service.to_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers=_attachment(export_service.filename("watchdog-register", "csv")),
    )


@router.get("/register/export.xlsx")
def export_xlsx(request: Request, store: Store, settings: Config) -> Any:
    """Exactly the current filter, as a workbook, streamed. Never written to disk."""
    rows = _export_rows(request, store, settings)
    workbook = export_service.to_xlsx(rows)
    return StreamingResponse(
        iter((workbook,)),
        media_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers=_attachment(export_service.filename("watchdog-register", "xlsx")),
    )


# ------------------------------------------------------------------- internals


def _params(request: Request) -> Mapping[str, object]:
    """The query string, keeping repeated keys such as ``band`` as a list."""
    return {key: request.query_params.getlist(key) for key in request.query_params}


def _is_partial(request: Request) -> bool:
    """True when HTMX asked for a fragment rather than a page."""
    return request.headers.get("HX-Request") == "true"


def _results(request: Request, view: RegisterView, *, reviewer: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(
        request, _RESULTS[view.query.mode], _context(request, view, reviewer=reviewer)
    )


def _context(
    request: Request, view: RegisterView, *, reviewer: str | None = None
) -> dict[str, Any]:
    return {
        "view": view,
        "query": view.query,
        "rows": view.rows,
        "sorts": query_service.sort_options(),
        "verdicts": list(Verdict),
        "closing_soon_days": query_service.CLOSING_SOON_DAYS,
        "version": _version(),
        # Whoever this browser is recording as. The card disables its verdict
        # buttons without one, so nothing can be saved with nobody's name on it.
        "reviewer": reviewer if reviewer is not None else reviewer_cookie.get_reviewer(request),
        # The header belt is rendered from the same partial the poller returns, so
        # a page load and a poll can never show different states.
        "job": view.job,
        "last_ingest": view.last_ingest,
        "last_screen": view.last_screen,
        "ai_enabled": view.ai_enabled,
        "busy_error": None,
        "finished": False,
    }


def _reviewer_panel(
    request: Request,
    reviewer: str | None,
    *,
    message: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "partials/reviewer.html",
        {"reviewer": reviewer, "reviewer_error": message, "version": _version()},
        status_code=status_code,
    )


def _version() -> str:
    from watchdog import __version__

    return __version__


def _export_rows(request: Request, store: Repository, settings: Settings) -> list[RegisterRow]:
    parsed = query_service.parse(_params(request), ai_enabled=query_service.ai_configured(settings))
    return query_service.rows_for_export(parsed, repository=store)


def _attachment(name: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{name}"'}


def _filter_problem(request: Request, exc: FilterError) -> HTMLResponse:
    return _problem(request, str(exc), status_code=400)


def _problem(request: Request, message: str, *, status_code: int) -> HTMLResponse:
    """A plain sentence and a way back. Never a stack trace."""
    return templates.TemplateResponse(
        request,
        "partials/problem.html",
        {"message": message, "version": _version()},
        status_code=status_code,
    )
