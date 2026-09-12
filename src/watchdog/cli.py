"""Command line entry point. Imports services and core only."""

from __future__ import annotations

import sys
import textwrap
from datetime import date
from typing import Annotated, Any

import typer

from watchdog import __version__
from watchdog.core.countries import country_name, country_names
from watchdog.core.enums import RunStatus, SourcePlatform
from watchdog.core.logging import configure_logging, get_logger
from watchdog.core.models import AxisScore, Run, SchemaCheck
from watchdog.core.settings import get_settings
from watchdog.services import ingest as ingest_service
from watchdog.services import screen as screen_service
from watchdog.services import ted as ted_service

app = typer.Typer(help="Watchdog - tender screening for Entr Advisory & Decision Support.")
config_app = typer.Typer(help="Inspect and check the configuration.")
quarantine_app = typer.Typer(help="Notices that could not be read, and recovering them.")
app.add_typer(config_app, name="config")
app.add_typer(quarantine_app, name="quarantine")

# What each run status means on screen, and the colour it is printed in.
_STATUS_COLOURS = {
    RunStatus.SUCCESS: typer.colors.GREEN,
    RunStatus.PARTIAL: typer.colors.YELLOW,
    RunStatus.FAILED: typer.colors.RED,
    RunStatus.RUNNING: typer.colors.CYAN,
}


@app.callback()
def main() -> None:
    """Watchdog. Run a command, or --help to see them all."""
    _survive_the_console_encoding()


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@config_app.command("show")
def config_show() -> None:
    """Show the effective configuration. Secrets are reported as set/unset only."""
    settings = get_settings()
    configure_logging(settings.log_level, json_output=not settings.is_dev)
    log = get_logger(__name__)
    log.info(
        "effective_configuration",
        database_url=settings.database_url,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
        llm_api_key_set=settings.llm_api_key is not None,
        llm_endpoint=settings.llm_endpoint,
        log_level=settings.log_level,
        data_dir=str(settings.data_dir),
        environment=settings.environment,
    )

    config = _load_ted_config()
    typer.echo("")
    typer.echo(f"TED source: {len(config.cpv_prefixes)} CPV code(s), searched equally.")
    typer.echo(f"  {' '.join(config.cpv_prefixes)}")

    if not config.provisional_cpv:
        typer.echo("  None of them is provisional.")
        return

    count = len(config.provisional_cpv)
    label = "1 code is" if count == 1 else f"{count} codes are"
    typer.secho(
        f"\n{label} provisional: kept on a stated bet rather than a measured gain, "
        "and owed an answer by the recall audit.",
        fg=typer.colors.YELLOW,
    )
    for entry in config.provisional_cpv:
        typer.echo(f"  {entry.code}  provisional since {entry.since.isoformat()}")
        typer.echo(
            textwrap.fill(entry.reason, width=88, initial_indent="    ", subsequent_indent="    ")
        )


@config_app.command("validate")
def config_validate() -> None:
    """Check the TED field list and query against the live API.

    TED requires a field list and rejects the whole request if one name is wrong,
    which otherwise shows up as an empty result set rather than as an error.
    """
    settings = get_settings()
    configure_logging(settings.log_level, json_output=not settings.is_dev)

    config = _load_ted_config()

    typer.echo(f"Checking {len(ted_service.REQUESTED_FIELDS)} TED fields against the live API...")

    try:
        result = ted_service.check_fields(config)
    except Exception as exc:
        typer.secho(f"Could not reach TED: {exc}", fg=typer.colors.RED)
        typer.echo("Check the network connection and try again. Nothing was changed.")
        raise typer.Exit(code=1) from exc

    if result.unknown:
        typer.secho("TED does not recognise these fields:", fg=typer.colors.RED)
        for field in result.unknown:
            typer.echo(f"  - {field}")
        typer.echo("Fix them in src/watchdog/sources/ted/mapper.py (REQUESTED_FIELDS).")
        raise typer.Exit(code=1)

    typer.secho("All requested fields are valid.", fg=typer.colors.GREEN)

    if not result.query_valid:
        typer.secho(f"TED rejected the query: {result.query_error}", fg=typer.colors.RED)
        typer.echo("Check cpv_prefixes, buyer_countries and extra_query in config/sources/ted.yaml")
        raise typer.Exit(code=1)

    typer.secho("The configured query is valid.", fg=typer.colors.GREEN)
    typer.echo(f"  {result.query}")


@app.command("ted-probe")
def ted_probe(
    days: int = typer.Option(
        3, "--days", min=1, max=90, help="How many days back to look, counting today."
    ),
    limit: int = typer.Option(25, "--limit", min=1, max=250, help="How many notices to print."),
) -> None:
    """Call TED live and print what came back. Stores nothing.

    A read-only look at the real API, for checking that the configured query
    finds the kind of notice you expect.
    """
    settings = get_settings()
    configure_logging(settings.log_level, json_output=not settings.is_dev)

    config = _load_ted_config()

    try:
        result = ted_service.probe(config, days=days, limit=limit)
    except Exception as exc:
        typer.secho(f"TED could not be read: {exc}", fg=typer.colors.RED)
        typer.echo("Nothing was stored. Try again, or run: watchdog config validate")
        raise typer.Exit(code=1) from exc

    window = f"{result.window_from.isoformat()} to {result.window_to.isoformat()}"
    typer.echo(f"Searching TED for {days} day(s), {window}. Nothing will be stored.")
    typer.secho(_probe_summary(result), fg=typer.colors.YELLOW if result.truncated else None)
    typer.echo("")

    for row in result.rows:
        country = country_name(row.buyer_country) or row.buyer_country or "unknown country"
        where = ", ".join(country_names(row.performance_countries)) or "not stated"
        typer.secho(f"{row.source_id}  buyer in {country}", fg=typer.colors.CYAN)
        typer.echo(f"  title    {row.title}")
        typer.echo(f"  work in  {where}")
        typer.echo(f"  cpv      {row.cpv_main or 'none'}")
        typer.echo(f"  all cpv  {', '.join(row.cpv_all) or 'none'}")
        typer.echo(f"  stage    {row.stage}")
        typer.echo(f"  deadline {row.deadline}  [{row.deadline_type}]")
        typer.echo("")

    typer.secho(_probe_summary(result), fg=typer.colors.YELLOW if result.truncated else None)
    typer.echo("Nothing was stored.")


def _probe_summary(result: ted_service.ProbeResult) -> str:
    """Say what was hidden. A listing that stops at the limit reads as the whole answer."""
    if not result.truncated:
        return f"Showing all {result.total} matching notice(s)."
    return (
        f"Showing {len(result.rows)} of {result.total} matching notices, "
        f"lowest publication numbers first. "
        f"{result.total - len(result.rows)} not shown - raise --limit to see more."
    )


@app.command("ingest")
def ingest(
    source: str = typer.Option(
        SourcePlatform.TED.value,
        "--source",
        help="Which source to read. Only 'ted' is available so far.",
    ),
    since_days: int | None = typer.Option(
        None,
        "--since-days",
        min=0,
        max=365,
        help="Ignore the watermark and reach this many days back instead.",
    ),
    full_backfill: str | None = typer.Option(
        None,
        "--full-backfill",
        metavar="YYYY-MM-DD",
        help="Ignore the watermark and start from this date, e.g. 2026-01-01.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Read the source and report what would change. Stores nothing.",
    ),
) -> None:
    """Fetch notices from a source into the register.

    Safe to run twice: the same notice keeps the same row, and nothing is ever
    deleted. If a run is interrupted, the next one repeats the same window.
    """
    settings = get_settings()
    configure_logging(settings.log_level, json_output=not settings.is_dev)

    if source.lower() != SourcePlatform.TED.value:
        typer.secho(f"There is no source called {source!r}.", fg=typer.colors.RED)
        typer.echo("Only --source ted is available so far. Nothing was changed.")
        raise typer.Exit(code=1)

    config = _load_ted_config()
    backfill_from = _parse_date(full_backfill, "--full-backfill")

    try:
        outcome = ingest_service.ingest_ted(
            config=config,
            since_days=since_days,
            backfill_from=backfill_from,
            dry_run=dry_run,
            on_progress=_report_progress,
        )
    except ingest_service.DatabaseNotReady as exc:
        _report_schema_problem(exc.check)
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        typer.secho(f"That window cannot be read: {exc}", fg=typer.colors.RED)
        typer.echo("Nothing was changed.")
        raise typer.Exit(code=1) from exc

    _report_outcome(outcome)

    if not outcome.ok:
        raise typer.Exit(code=1)


@app.command("screen")
def screen(
    stage: int = typer.Option(
        3, "--stage", help="3 screens and stores. 2 assesses a sample and stores nothing."
    ),
    limit: int = typer.Option(
        0, "--limit", min=0, help="How many notices to handle. 0 means every one that needs it."
    ),
    rescreen: bool = typer.Option(
        False, "--rescreen", help="Screen every notice again, not only the ones that are stale."
    ),
    explain: bool = typer.Option(
        False,
        "--explain",
        help="Print the three judgements, their quotes and the confidence for each notice.",
    ),
    no_cache: bool = typer.Option(
        False, "--no-cache", help="Ignore stored answers and call the model again."
    ),
) -> None:
    """Screen the register: the rules, the model where there is one, then the score.

    Stage 3 is the whole thing and it stores one screening result per notice. Only
    notices whose current result no longer reflects how we screen today are done,
    so running it twice in a row is cheap; --rescreen does the lot.

    Stage 2 assesses a sample and stores nothing. It exists so a person can read
    twenty assessments with --explain before anything is banded on them.
    """
    settings = get_settings()
    configure_logging(settings.log_level, json_output=not settings.is_dev)

    if stage not in (2, 3):
        typer.secho(f"There is no screening stage {stage}.", fg=typer.colors.RED)
        typer.echo(
            "Stage 1 is the deterministic rules and runs as part of both. Stage 2 is the "
            "model assessment on a sample. Stage 3 is the score and the band, stored. "
            "Nothing was changed."
        )
        raise typer.Exit(code=1)

    if stage == 2:
        _screen_sample(limit=limit or 20, explain=explain, no_cache=no_cache)
        return

    try:
        outcome = screen_service.screen_register(
            limit=limit or None,
            rescreen=rescreen,
            use_cache=not no_cache,
            on_progress=_screening_progress,
        )
    except ingest_service.DatabaseNotReady as exc:
        _report_schema_problem(exc.check)
        raise typer.Exit(code=1) from exc
    except screen_service.LLMError as exc:
        typer.secho(f"The model could not be used: {exc}", fg=typer.colors.RED)
        typer.echo("Check LLM_PROVIDER and its settings, then try again.")
        raise typer.Exit(code=1) from exc

    _report_screening(outcome)


def _screen_sample(*, limit: int, explain: bool, no_cache: bool) -> None:
    try:
        outcome = screen_service.assess_sample(limit=limit, use_cache=not no_cache)
    except ingest_service.DatabaseNotReady as exc:
        _report_schema_problem(exc.check)
        raise typer.Exit(code=1) from exc
    except screen_service.LLMError as exc:
        typer.secho(f"The model could not be used: {exc}", fg=typer.colors.RED)
        typer.echo("Nothing was stored. Check LLM_PROVIDER and its settings, then try again.")
        raise typer.Exit(code=1) from exc

    _report_assessment(outcome, explain=explain)

    if not outcome.ok:
        raise typer.Exit(code=1)


@app.command("runs")
def runs(
    limit: int = typer.Option(20, "--limit", min=1, max=200, help="How many runs to show."),
) -> None:
    """Show recent runs, with what each one found and anything it could not do."""
    settings = get_settings()
    configure_logging(settings.log_level, json_output=not settings.is_dev)

    try:
        history = ingest_service.recent_runs(limit=limit)
    except ingest_service.DatabaseNotReady as exc:
        _report_schema_problem(exc.check)
        raise typer.Exit(code=1) from exc

    if not history:
        typer.echo("No runs yet. Start one with: watchdog ingest --source ted")
        return

    for run in history:
        _print_run(run)


@quarantine_app.command("list")
def quarantine_list(
    limit: int = typer.Option(50, "--limit", min=1, max=500, help="How many to show."),
    include_resolved: bool = typer.Option(
        False, "--all", help="Include notices that have since been recovered."
    ),
) -> None:
    """Show notices that could not be read, with the reason each one failed."""
    settings = get_settings()
    configure_logging(settings.log_level, json_output=not settings.is_dev)

    try:
        notices = ingest_service.list_quarantined(limit=limit, include_resolved=include_resolved)
    except ingest_service.DatabaseNotReady as exc:
        _report_schema_problem(exc.check)
        raise typer.Exit(code=1) from exc

    if not notices:
        typer.secho(
            "Nothing in quarantine. Every notice read so far was understood.", fg=typer.colors.GREEN
        )
        return

    for item in notices:
        state = "recovered" if item.resolved else "needs attention"
        colour = typer.colors.GREEN if item.resolved else typer.colors.YELLOW
        typer.secho(f"{item.id}  {state}", fg=colour)
        typer.echo(f"  reason    {item.error}  [{item.error_type}]")
        typer.echo(f"  seen      {item.first_seen_at.strftime('%Y-%m-%d %H:%M')} UTC")
        typer.echo(f"  from run  {item.run_id or 'unknown'}")

    unresolved = sum(1 for item in notices if not item.resolved)
    typer.echo("")
    typer.echo(f"{unresolved} notice(s) need attention. The full notice is stored for each one.")
    typer.echo("Once the mapping is fixed, recover them with: watchdog quarantine retry")


@quarantine_app.command("retry")
def quarantine_retry(
    ids: Annotated[
        list[str] | None,
        typer.Option(
            "--id",
            help="Retry only these, as shown by 'quarantine list'. Repeat for several.",
        ),
    ] = None,
    limit: int = typer.Option(200, "--limit", min=1, max=1000, help="How many to try at once."),
) -> None:
    """Read stored payloads again with today's mapping.

    Fetches nothing and never changes how far a source has been read, so it is
    safe to run at any time.
    """
    settings = get_settings()
    configure_logging(settings.log_level, json_output=not settings.is_dev)

    try:
        outcome = ingest_service.retry_quarantined(ids=ids or None, limit=limit)
    except ingest_service.DatabaseNotReady as exc:
        _report_schema_problem(exc.check)
        raise typer.Exit(code=1) from exc

    if not outcome.counts["attempted"]:
        typer.echo("Nothing to retry.")
        return

    for item in outcome.results:
        if item.outcome == "recovered":
            typer.secho(f"  recovered  {item.source_id}", fg=typer.colors.GREEN)
        elif item.outcome == "already_current":
            typer.secho(f"  already in the register  {item.source_id}", fg=typer.colors.GREEN)
        elif item.outcome == "still_failing":
            typer.secho(
                f"  still unreadable  {item.source_id}: {item.reason}", fg=typer.colors.YELLOW
            )
        else:
            typer.secho(
                f"  could not be handled  {item.source_id}: {item.reason}", fg=typer.colors.RED
            )

    counts = outcome.counts
    typer.echo("")
    typer.echo(
        f"{counts['attempted']} tried: {counts['recovered']} recovered, "
        f"{counts['already_current']} already current, "
        f"{counts['still_failing']} still unreadable."
    )
    typer.echo("How far each source has been read was not changed.")

    if not outcome.ok:
        typer.secho(
            f"The retry did not finish cleanly ({outcome.status.value}).", fg=typer.colors.RED
        )
        raise typer.Exit(code=1)


def _report_gap(outcome: ingest_service.IngestOutcome) -> None:
    """Say which days this window does not cover, and how to collect them."""
    gap_from = outcome.window.gap_from
    if gap_from is None:
        return

    missing = outcome.window.window_from - gap_from
    tense = "does not cover" if outcome.dry_run else "did not collect"

    typer.secho(
        f"This window starts after the last successful run, so it {tense} "
        f"{missing.days} day(s) of notices: "
        f"{gap_from.strftime('%Y-%m-%d %H:%M')} to "
        f"{outcome.window.window_from.strftime('%Y-%m-%d %H:%M')} UTC.",
        fg=typer.colors.YELLOW,
    )
    typer.echo(
        "How far the source has been read is left where it is, so nothing is "
        "skipped. Collect everything outstanding with:"
    )
    typer.echo(f"  watchdog ingest --since-days {missing.days + 1}")


def _report_schema_problem(check: SchemaCheck) -> None:
    if check.empty:
        typer.secho("The database has not been set up yet.", fg=typer.colors.RED)
    else:
        typer.secho("The database schema is out of date.", fg=typer.colors.RED)
        typer.echo(f"  It does not have: {check.summary}")

    typer.echo("Run this once, then try again:")
    typer.echo("  .\\tasks.ps1 migrate      (on Windows)")
    typer.echo("  make migrate             (on Linux or in a container)")
    typer.echo("Nothing was changed.")


def _parse_date(value: str | None, option: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        typer.secho(f"{option} could not be read as a date: {value!r}", fg=typer.colors.RED)
        typer.echo("Write it as YYYY-MM-DD, for example 2026-01-01. Nothing was changed.")
        raise typer.Exit(code=1) from exc


def _report_progress(counts: ingest_service.IngestCounts) -> None:
    typer.echo(f"  {counts.fetched} notice(s) read so far...")


def _report_outcome(outcome: ingest_service.IngestOutcome) -> None:
    counts = outcome.counts
    window_from, window_to = outcome.window.query_dates

    typer.echo("")
    typer.echo(
        f"Read notices published {window_from.isoformat()} to {window_to.isoformat()} "
        f"({outcome.window.reason})."
    )

    if outcome.dry_run:
        typer.secho(
            f"Dry run: {counts.fetched} notice(s) found, {counts.new} not in the register "
            f"yet and {counts.updated} already there. Nothing was stored.",
            fg=typer.colors.CYAN,
        )
    else:
        typer.echo(
            f"{counts.fetched} notice(s) read: {counts.new} new, "
            f"{counts.updated} updated, {counts.unchanged} unchanged."
        )

    if outcome.quarantined:
        typer.secho(
            f"{len(outcome.quarantined)} notice(s) could not be read and were set aside:",
            fg=typer.colors.YELLOW,
        )
        for item in outcome.quarantined:
            typer.echo(f"  - {item.source_id}: {item.error}")
        typer.echo("  Their payloads are stored. See them with: watchdog quarantine list")

    if outcome.ok:
        if outcome.quarantined:
            # A bare "Done" would be read as "nothing to look at", and nobody
            # reads run counts by choice.
            typer.secho(
                f"Completed with warnings: {len(outcome.quarantined)} notice(s) need attention.",
                fg=typer.colors.YELLOW,
            )
        else:
            typer.secho("Done.", fg=typer.colors.GREEN)

        if outcome.watermark_advanced:
            typer.echo(
                "The next run will start from "
                f"{outcome.window.window_to.strftime('%Y-%m-%d %H:%M')} UTC, less the overlap."
            )
            return

        # A dry run is exactly when someone is checking whether a window is safe,
        # so both reasons are worth saying: it stored nothing, and it would have
        # left days behind even if it had.
        if outcome.window.gap_from is not None:
            _report_gap(outcome)
        if outcome.dry_run:
            typer.echo("Nothing was stored, so how far the source has been read is unchanged.")
        return

    colour = _STATUS_COLOURS.get(outcome.status, typer.colors.RED)
    typer.secho(f"The run did not finish ({outcome.status.value}).", fg=colour)
    for message in outcome.errors:
        if not message.startswith("quarantined "):
            typer.echo(f"  - {message}")
    typer.echo(
        "Nothing was lost. The next run reads the same window again, "
        "so run it once the problem is fixed: watchdog ingest --source ted"
    )


def _survive_the_console_encoding() -> None:
    """Never let a notice in Norwegian stop a listing halfway through.

    Notice text is stored in its original language, and a Windows console is
    often still on a legacy code page that cannot represent it. Replacing the
    characters it cannot print loses an accent; raising loses the listing.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


def _report_assessment(outcome: screen_service.SampleOutcome, *, explain: bool) -> None:
    """Say what the model judged, or say plainly that no model was involved."""
    run = outcome.run
    counts = run.counts

    typer.echo("")
    if not outcome.ai_enabled:
        typer.secho(
            "AI assessment is off, so nothing was assessed.",
            fg=typer.colors.YELLOW,
        )
        typer.echo(
            f"{counts['skipped']} notice(s) would be screened on the deterministic rules alone. "
            "To switch a model on, set LLM_PROVIDER, LLM_MODEL, LLM_ENDPOINT and LLM_API_KEY."
        )
        return

    typer.echo(
        f"Provider {run.provider}, model {run.model or 'not named'}, "
        f"prompt {run.prompt_version}, rules v{outcome.rules_version}, "
        f"profile v{outcome.profile_version}."
    )
    typer.echo(
        f"Read {outcome.considered} notice(s) from the register: "
        f"{counts['assessed']} assessed, {counts['cached']} answered from the cache, "
        f"{counts['repaired']} needed a second attempt, {counts['failed']} could not be "
        f"assessed, {counts['skipped']} were not worth a model call."
    )
    if not outcome.cache_enabled:
        typer.echo("  The cache was ignored for this run (--no-cache).")
    typer.echo(
        f"  {run.tokens_in} token(s) in, {run.tokens_out} out, "
        f"{run.latency_ms / 1000:.1f}s waiting for the model."
    )
    typer.secho(
        "  Nothing was stored: this is the sample. Run `watchdog screen` to score and store.",
        fg=typer.colors.CYAN,
    )

    if explain:
        for item in run.results:
            _print_assessment(item)

    for item in run.failed:
        typer.secho(f"  could not assess {item.tender_id}: {item.error}", fg=typer.colors.YELLOW)

    if run.failed:
        typer.echo("  Those notices are left unassessed and a later run will try them again.")


def _screening_progress(done: int, total: int) -> None:
    typer.echo(f"  screened {done} of {total}...")


def _report_screening(outcome: screen_service.ScreenOutcome) -> None:
    """What one full screening run did, in the order a person wants to read it."""
    typer.echo("")
    typer.echo(
        f"Provider {outcome.provider}, model {outcome.model or 'not named'}, "
        f"rules v{outcome.versions.rules_version}, policy v{outcome.versions.policy_version}, "
        f"profile v{outcome.versions.profile_version}."
    )

    if outcome.screened == 0:
        typer.secho(
            "Every notice in the register is already screened at these versions. "
            "Nothing was changed.",
            fg=typer.colors.GREEN,
        )
        typer.echo("  To screen them all again: watchdog screen --rescreen")
        return

    typer.echo(
        f"Screened {outcome.screened} notice(s); "
        f"{outcome.already_current} were already current and were not touched."
    )

    if not outcome.ai_enabled:
        typer.secho(
            "No model is configured, so these were screened on the keyword rules alone.",
            fg=typer.colors.YELLOW,
        )
        typer.echo(
            "  Nothing is shortlisted in this mode: a shortlist claims a judgement that "
            "nothing has made. Switch a model on with LLM_PROVIDER and every result here "
            "is re-screened on the next run."
        )
    else:
        typer.echo(
            f"  {outcome.tokens_in} token(s) in, {outcome.tokens_out} out. "
            f"{outcome.to_retry} notice(s) could not be assessed and will be tried again."
        )

    typer.echo("")
    typer.secho("By band", bold=True)
    for band in ("shortlist", "review", "archive"):
        typer.echo(f"  {band:<12} {outcome.by_band.get(band, 0)}")

    typer.echo("")
    typer.secho("By score", bold=True)
    for score, count in outcome.by_score.items():
        typer.echo(f"  {score:<12} {count}")

    if outcome.by_rules_only_score:
        typer.echo("")
        typer.secho("By rules priority", bold=True)
        typer.echo("  What the keyword rules alone claimed. Not a judged score.")
        for grade, count in outcome.by_rules_only_score.items():
            typer.echo(f"  {grade:<12} {count}{'   (no evidence at all)' if grade == 0 else ''}")

    typer.echo("")
    typer.secho("By reason code", bold=True)
    if not outcome.by_reason_code:
        typer.echo("  none")
    for code, count in outcome.by_reason_code.items():
        typer.echo(f"  {code:<26} {count}")

    if not outcome.top:
        return

    typer.echo("")
    typer.secho(
        f"Top {len(outcome.top)} in register order ({outcome.priority_label} first)", bold=True
    )
    for position, row in enumerate(outcome.top, start=1):
        title = textwrap.shorten(row.title, width=74, placeholder=" ...")
        typer.echo(f"  {position:>2}. [{row.priority}] {title}")
        evidence = ", ".join(row.domain_rules) or "no domain rule matched"
        typer.echo(f"      {row.tender_id}  {row.band.value}  {evidence}")


def _print_assessment(item: screen_service.AssessmentOutcome) -> None:
    typer.echo("")
    typer.secho(textwrap.shorten(item.title, width=96, placeholder=" ..."), fg=typer.colors.CYAN)
    typer.echo(f"  {item.tender_id}{'  (from the cache)' if item.cached else ''}")

    assessment = item.assessment
    if assessment is None:
        typer.secho(f"  not assessed: {item.error}", fg=typer.colors.YELLOW)
        return

    for name, axis in (
        ("domain", assessment.domain_fit),
        ("service", assessment.service_fit),
        ("stage", assessment.stage_fit),
    ):
        _print_axis(name, axis)

    for signal in assessment.negative_signals:
        typer.secho(f"  against  {signal}", fg=typer.colors.YELLOW)
    for gap in assessment.missing_information:
        typer.echo(f"  missing  {gap}")

    typer.echo(f"  reason   {assessment.short_reason}")
    typer.echo(f"  confidence {item.confidence:.2f}")
    for reason in item.confidence_reasons:
        typer.echo(f"    - {reason}")


def _print_axis(name: str, axis: AxisScore[Any]) -> None:
    score = "not established" if axis.score is None else f"{axis.score}/5"
    typer.echo(f"  {name:<8} {score:<16} {axis.label.value}")
    for quote in axis.evidence:
        shortened = textwrap.shorten(quote, width=88, placeholder=" ...")
        typer.echo(f"           \u201c{shortened}\u201d")


def _print_run(run: Run) -> None:
    started = run.started_at.strftime("%Y-%m-%d %H:%M")
    where = run.source.value if run.source is not None else "-"
    colour = _STATUS_COLOURS.get(run.status, typer.colors.WHITE)

    typer.secho(
        f"{started} UTC  {run.kind.value:<6}  {where:<4}  {run.status.value}",
        fg=colour,
    )

    counts = ", ".join(f"{name} {value}" for name, value in run.counts.items())
    typer.echo(f"  {counts or 'no counts recorded'}")

    if run.provider is not None:
        typer.echo(
            f"  judged by {run.provider}/{run.model or 'unnamed model'}"
            f" under prompt {run.prompt_version or 'unknown'}"
            f"  {run.tokens_in} token(s) in, {run.tokens_out} out"
        )

    if run.window_from is not None and run.window_to is not None:
        typer.echo(
            f"  window   {run.window_from.strftime('%Y-%m-%d %H:%M')}"
            f" to {run.window_to.strftime('%Y-%m-%d %H:%M')} UTC"
            f"  {'watermark moved' if run.watermark_advanced else 'watermark held'}"
        )

    for message in run.errors:
        typer.echo(f"  - {message}")


def _load_ted_config() -> ted_service.TedSourceConfig:
    try:
        return ted_service.get_config()
    except (OSError, ValueError) as exc:
        typer.secho(f"config/sources/ted.yaml could not be read: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    app()
