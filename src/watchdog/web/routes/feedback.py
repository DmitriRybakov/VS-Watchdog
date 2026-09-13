"""Feedback mode: a toggle, a comment box, a list, and an export.

The toggle is a cookie, not a setting: it is per browser, off by default, and
turning it off hides the controls and keeps every comment.

Nothing was added to individual templates to make this work. While the mode is
on, the script in ``static/js/feedback.js`` intercepts a click anywhere, reads the
element's identifier and its visible label, and opens the box. That is what makes
it removable: delete the script, the partial, this module and the service, drop
the table, and take one include out of the base template.

Two safety rules, both enforced here as well as in the browser:

- **An intercepted click never runs.** The script stops the action; this module
  never performs one either, so a comment on "Update from TED" cannot start an
  ingest even if the browser's half were bypassed.
- **Only an identifier and a visible label are stored.** Never the contents of an
  input, which is how a password ends up in a comments table.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from starlette.responses import Response

from watchdog import __version__
from watchdog.services import feedback as feedback_service
from watchdog.services.feedback import EmptyComment
from watchdog.web.deps import Store
from watchdog.web.reviewer import Reviewer
from watchdog.web.templating import templates

router = APIRouter(tags=["feedback"])

COOKIE = "watchdog_feedback"

# A year, like the reviewer's name. Long enough that nobody re-enables it every
# morning, and it is only a display preference either way.
COOKIE_MAX_AGE = 365 * 24 * 60 * 60


def is_on(request: Request) -> bool:
    """Whether this browser is in feedback mode. Off unless it says otherwise."""
    return request.cookies.get(COOKIE) == "on"


@router.post("/feedback/mode", response_class=HTMLResponse)
def toggle(request: Request, on: str = Form(default="")) -> HTMLResponse:
    """Turn feedback mode on or off for this browser. Comments are never affected."""
    wanted = on in {"1", "on", "true", "yes"}
    response = templates.TemplateResponse(
        request,
        "partials/feedback_toggle.html",
        {"feedback_on": wanted, "version": __version__},
    )
    if wanted:
        response.set_cookie(COOKIE, "on", max_age=COOKIE_MAX_AGE, samesite="lax")
    else:
        response.delete_cookie(COOKIE)
    return response


@router.post("/feedback", response_class=HTMLResponse)
def record(
    request: Request,
    store: Store,
    reviewer: Reviewer,
    page: str = Form(default=""),
    element: str = Form(default=""),
    element_label: str = Form(default=""),
    tender_id: str = Form(default=""),
    comment: str = Form(default=""),
) -> HTMLResponse:
    """Store one comment and say so. Performs no action of any kind."""
    try:
        saved = feedback_service.record(
            page=page,
            element=element,
            element_label=element_label or None,
            comment=comment,
            tender_id=tender_id or None,
            reported_by=reviewer,
            repository=store,
        )
    except EmptyComment as exc:
        return templates.TemplateResponse(
            request,
            "partials/feedback_result.html",
            {"problem": str(exc), "saved": None, "version": __version__},
            status_code=400,
        )

    return templates.TemplateResponse(
        request,
        "partials/feedback_result.html",
        {"problem": None, "saved": saved, "version": __version__},
    )


@router.get("/feedback", response_class=HTMLResponse)
def listing(request: Request, store: Store) -> HTMLResponse:
    """Every comment, grouped by page and by what was clicked."""
    return templates.TemplateResponse(
        request,
        "feedback.html",
        {
            "pages": feedback_service.grouped(repository=store),
            "feedback_on": is_on(request),
            "version": __version__,
        },
    )


@router.get("/feedback/export.md")
def export(store: Store) -> Response:
    """The comments as Markdown, streamed. Never written to disk."""
    pages = feedback_service.grouped(repository=store)
    return StreamingResponse(
        feedback_service.to_markdown(pages),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="watchdog-feedback.md"'},
    )
