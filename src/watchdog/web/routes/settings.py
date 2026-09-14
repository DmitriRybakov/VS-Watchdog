"""Settings: the rule set, the scoring policy and the mandate, edited in the browser.

The active configuration lives in the database, not in the YAML files. This is
where a colleague changes it without a terminal, and where the two things that
protect the register from that convenience live:

- **A save writes a new version.** Nothing is edited and nothing is deleted, so a
  screening result stamped ``rules 3`` can still be read against the rules that
  produced it. The diff is shown before the save, not after.
- **The rule vocabulary is frozen.** A save is refused unless the override is
  ticked, and the refusal says why. Without it a settings page would quietly
  produce version 4 while step 7's scoring policy is being tuned against
  version 3, and nobody would be able to say afterwards which change moved the
  distribution.

Every route here is behind the same sign-in and the same CSRF check as the rest of
the site; both are applied by the middleware and by the shared header, so there is
nothing to remember on a new route.

The staleness count is loaded as a separate fragment on purpose. It walks every
notice in the register, and a save that waited for it would look like a save that
had hung.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.background import BackgroundTask

from watchdog import __version__
from watchdog.core.enums import ConfigKind, RunKind
from watchdog.core.settings import Settings
from watchdog.services import configuration as config_service
from watchdog.services import jobs
from watchdog.services import query as query_service
from watchdog.services import ted as ted_service
from watchdog.services.configuration import ConfigFrozen, ConfigInvalid
from watchdog.storage.repository import Repository
from watchdog.web.deps import Config, Store
from watchdog.web.reviewer import Reviewer
from watchdog.web.templating import templates

router = APIRouter(tags=["settings"])

# What each configuration is called on screen, and the one sentence that says what
# changing it does to the register.
KIND_LABELS: dict[ConfigKind, str] = {
    ConfigKind.RULES: "Keyword rules",
    ConfigKind.POLICY: "Scoring policy",
    ConfigKind.PROFILE: "Mandate",
}

KIND_NOTES: dict[ConfigKind, str] = {
    ConfigKind.RULES: (
        "The vocabulary the deterministic stage matches on. Keywords are evidence, never a "
        "verdict: nothing here rejects a notice on its own."
    ),
    ConfigKind.POLICY: (
        "The weights, caps and bands that turn three axis scores into one number. Changing "
        "these changes the arithmetic, never the judgement behind it."
    ),
    ConfigKind.PROFILE: (
        "What Entr is for, in the words a model is given verbatim. Part 2 of the file, the "
        "commercial reality, is never sent to a model and is not editable here."
    ),
}


@router.get("/settings", response_class=HTMLResponse)
def overview(request: Request, store: Store, settings: Config) -> HTMLResponse:
    """The three configurations, their versions, and what a change has made stale."""
    return templates.TemplateResponse(request, "settings.html", _overview(store, settings))


@router.get("/settings/staleness", response_class=HTMLResponse)
def staleness(request: Request, store: Store, settings: Config) -> HTMLResponse:
    """How many stored results no longer reflect the active configuration.

    Its own fragment because it reads every notice in the register. Loaded after
    the page, so the page is never waiting on it.
    """
    return templates.TemplateResponse(
        request, "partials/staleness.html", _staleness(store, settings)
    )


@router.post("/settings/rescreen", response_class=HTMLResponse)
def rescreen(request: Request, store: Store, settings: Config) -> HTMLResponse:
    """Screen the affected records, and only those. Never the whole register."""
    error: str | None = None
    started = False
    try:
        jobs.claim(jobs.RESCREEN_STALE, repository=store)
        started = True
    except jobs.JobBusy as exc:
        error = str(exc)

    if started:
        # Started in the background so the request returns at once; the header belt
        # on every page polls the same lock row and shows the progress.
        response = templates.TemplateResponse(
            request,
            "partials/staleness.html",
            {**_staleness(store, settings), "started": True},
        )
        response.background = BackgroundTask(
            jobs.run, jobs.RESCREEN_STALE, repository=store, settings=settings
        )
        return response

    return templates.TemplateResponse(
        request,
        "partials/staleness.html",
        {**_staleness(store, settings), "busy_error": error},
    )


@router.get("/settings/{kind}", response_class=HTMLResponse)
def edit(request: Request, kind: str, store: Store, settings: Config) -> HTMLResponse:
    """The editor for one configuration."""
    chosen = _kind(kind)
    if chosen is None:
        return _problem(request, _unknown_kind(kind), status_code=404)

    return templates.TemplateResponse(
        request, f"settings_{chosen.value}.html", _editor(chosen, store, settings)
    )


@router.post("/settings/{kind}/preview", response_class=HTMLResponse)
async def preview(request: Request, kind: str, store: Store) -> HTMLResponse:
    """The change a save would make, before it is made."""
    chosen = _kind(kind)
    if chosen is None:
        return _problem(request, _unknown_kind(kind), status_code=404)

    form = dict(await request.form())
    try:
        proposed = _proposed(chosen, form, store)
    except ConfigInvalid as exc:
        return _diff(request, chosen, [], problems=exc.problems)

    return _diff(request, chosen, config_service.diff(chosen, proposed, repository=store))


@router.post("/settings/{kind}", response_class=HTMLResponse)
async def save(request: Request, kind: str, store: Store, reviewer: Reviewer) -> HTMLResponse:
    """Validate, then store a new version. Refused while the vocabulary is frozen."""
    chosen = _kind(kind)
    if chosen is None:
        return _problem(request, _unknown_kind(kind), status_code=404)

    form = dict(await request.form())
    note = str(form.get("note") or "").strip() or None
    override = str(form.get("override_freeze") or "") in {"1", "on", "true", "yes"}

    try:
        proposed = _proposed(chosen, form, store)
        saved = config_service.save(
            chosen,
            proposed,
            saved_by=reviewer or "",
            note=note,
            override_freeze=override,
            repository=store,
        )
    except ConfigFrozen as exc:
        return _saved_panel(request, chosen, frozen=str(exc))
    except ConfigInvalid as exc:
        return _saved_panel(request, chosen, problems=exc.problems)

    return _saved_panel(request, chosen, version=saved.version)


# ------------------------------------------------------------------- internals


def _kind(value: str) -> ConfigKind | None:
    try:
        return ConfigKind(value)
    except ValueError:
        return None


def _unknown_kind(value: str) -> str:
    names = ", ".join(member.value for member in ConfigKind)
    return f"{value!r} is not a configuration Watchdog holds. The ones it holds are: {names}."


def _proposed(kind: ConfigKind, form: dict[str, Any], store: Repository) -> dict[str, Any]:
    """The submitted form as a whole configuration payload, ready to validate."""
    current = config_service.active(kind, repository=store).payload

    if kind is ConfigKind.POLICY:
        numbers = {
            field.path: str(form.get(f"number.{field.path}") or "")
            for field in config_service.POLICY_NUMBERS
        }
        caps = {
            name.removeprefix("cap."): str(value)
            for name, value in form.items()
            if name.startswith("cap.")
        }
        return config_service.apply_policy_form(current, numbers, caps)

    if kind is ConfigKind.PROFILE:
        return config_service.apply_profile_form(
            current,
            principle=str(form.get("principle") or ""),
            mandate_yaml=str(form.get("mandate") or ""),
        )

    return _proposed_rules(current, form)


def _proposed_rules(current: dict[str, Any], form: dict[str, Any]) -> dict[str, Any]:
    """The rules payload with the submitted per-rule edits applied.

    A rule is only touched when the form carried its marker. A browser leaves an
    unchecked box out of the submission entirely, so without the marker a partial
    post would read as "every rule has no aliases and is inactive" - which would
    validate as an error at best and wipe the vocabulary at worst.
    """
    edits: dict[str, dict[str, Any]] = {}
    removed: set[str] = set()

    for rule in current.get("rules") or []:
        identifier = str(rule.get("id", ""))
        if not identifier or not form.get(f"rule.{identifier}.present"):
            continue
        if form.get(f"rule.{identifier}.remove"):
            removed.add(identifier)
            continue
        edits[identifier] = {
            "aliases": config_service.lines_to_terms(
                str(form.get(f"rule.{identifier}.aliases", ""))
            ),
            "requires_context": config_service.lines_to_terms(
                str(form.get(f"rule.{identifier}.context", ""))
            ),
            "requires_companion": config_service.lines_to_terms(
                str(form.get(f"rule.{identifier}.companion", ""))
            ),
            "blocked_by": config_service.lines_to_terms(
                str(form.get(f"rule.{identifier}.blocked", ""))
            ),
            "strength": str(form.get(f"rule.{identifier}.strength") or rule.get("strength")),
            "active": bool(form.get(f"rule.{identifier}.active")),
            "note": str(form.get(f"rule.{identifier}.note") or "").strip() or None,
        }

    added: dict[str, Any] | None = None
    new_id = str(form.get("new.id") or "").strip()
    if new_id:
        added = {
            "id": new_id,
            "signal": str(form.get("new.signal") or "domain"),
            "label": str(form.get("new.label") or "").strip() or None,
            "strength": str(form.get("new.strength") or "medium"),
            "aliases": config_service.lines_to_terms(str(form.get("new.aliases", ""))),
            "requires_context": config_service.lines_to_terms(str(form.get("new.context", ""))),
            "active": True,
            "note": str(form.get("new.note") or "").strip() or None,
        }

    return config_service.apply_rules_form(current, edits, removed=removed, added=added)


def _overview(store: Repository, settings: Settings) -> dict[str, Any]:
    versions = {kind: config_service.active(kind, repository=store) for kind in ConfigKind}

    # What we ask TED for. Read-only for now, but visible: without it a colleague
    # cannot tell whether a notice that is not here was screened out or never
    # fetched, and those have different fixes.
    try:
        conditions: ted_service.FetchConditions | None = ted_service.fetch_conditions()
        conditions_error: str | None = None
    except (OSError, ValueError) as exc:
        conditions = None
        conditions_error = str(exc)

    return {
        "kinds": list(ConfigKind),
        "labels": KIND_LABELS,
        "notes": KIND_NOTES,
        "versions": versions,
        "fetch": conditions,
        "fetch_error": conditions_error,
        "history": {kind: config_service.history(kind, repository=store) for kind in ConfigKind},
        "frozen_at": config_service.FROZEN_RULES_VERSION,
        "freeze_reason": config_service.FREEZE_REASON,
        "job": store.get_job(query_service.PIPELINE_JOB),
        "last_ingest": store.latest_run(RunKind.INGEST),
        "last_screen": store.latest_run(RunKind.SCREEN),
        "ai_enabled": query_service.ai_configured(settings),
        "busy_error": None,
        "finished": False,
        "version": __version__,
    }


def _editor(kind: ConfigKind, store: Repository, settings: Settings) -> dict[str, Any]:
    version = config_service.active(kind, repository=store)
    context: dict[str, Any] = {
        "kind": kind,
        "label": KIND_LABELS[kind],
        "note": KIND_NOTES[kind],
        "version": __version__,
        "active": version,
        "frozen_at": config_service.FROZEN_RULES_VERSION,
        "freeze_reason": config_service.FREEZE_REASON,
        "is_frozen": kind is ConfigKind.RULES,
        "ai_enabled": query_service.ai_configured(settings),
        "job": store.get_job(query_service.PIPELINE_JOB),
        "last_ingest": store.latest_run(RunKind.INGEST),
        "last_screen": store.latest_run(RunKind.SCREEN),
        "busy_error": None,
        "finished": False,
    }

    if kind is ConfigKind.POLICY:
        context["numbers"] = config_service.POLICY_NUMBERS
        context["payload"] = version.payload
        context["caps"] = config_service.policy_caps(version.payload)
    elif kind is ConfigKind.PROFILE:
        context["principle"] = version.payload.get("principle", "")
        context["mandate"] = config_service.mandate_yaml(version.payload)
    else:
        context["rules"] = version.payload.get("rules") or []
        context["matching"] = version.payload.get("matching") or {}

    return context


def _staleness(store: Repository, settings: Settings) -> dict[str, Any]:
    snapshot = config_service.snapshot(repository=store)
    enabled = query_service.ai_configured(settings)
    provider = settings.llm_provider if enabled else "disabled"
    model = settings.llm_model if enabled else None

    report = store.screening_staleness(
        snapshot.versions(prompt_version=None if not enabled else _prompt_version()),
        provider,
        model,
    )
    return {
        "staleness": report,
        "job": store.get_job(query_service.PIPELINE_JOB),
        "ai_enabled": enabled,
        "busy_error": None,
        "started": False,
        "finished": False,
        "version": __version__,
    }


def _prompt_version() -> str:
    from watchdog.screening.prompts import DEFAULT_PROMPT_VERSION

    return DEFAULT_PROMPT_VERSION


def _diff(
    request: Request,
    kind: ConfigKind,
    lines: list[str],
    *,
    problems: list[str] | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "partials/config_diff.html",
        {
            "kind": kind,
            "label": KIND_LABELS[kind],
            "diff": lines,
            "problems": problems or [],
            "version": __version__,
        },
    )


def _saved_panel(
    request: Request,
    kind: ConfigKind,
    *,
    version: int | None = None,
    problems: list[str] | None = None,
    frozen: str | None = None,
) -> HTMLResponse:
    status_code = 200 if version is not None else 400
    return templates.TemplateResponse(
        request,
        "partials/config_saved.html",
        {
            "kind": kind,
            "label": KIND_LABELS[kind],
            "saved_version": version,
            "problems": problems or [],
            "frozen": frozen,
            "version": __version__,
        },
        status_code=status_code,
    )


def _problem(request: Request, message: str, *, status_code: int) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "partials/problem.html",
        {"message": message, "version": __version__},
        status_code=status_code,
    )
