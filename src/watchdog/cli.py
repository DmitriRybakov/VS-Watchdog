"""Command line entry point. Imports services and core only."""

from __future__ import annotations

from datetime import date

import typer

from watchdog import __version__
from watchdog.core.countries import country_name, country_names
from watchdog.core.enums import RunStatus, SourcePlatform
from watchdog.core.logging import configure_logging, get_logger
from watchdog.core.models import Run
from watchdog.core.settings import get_settings
from watchdog.services import ingest as ingest_service
from watchdog.services import ted as ted_service

app = typer.Typer(help="Watchdog - tender screening for Entr Advisory & Decision Support.")
config_app = typer.Typer(help="Inspect and check the configuration.")
app.add_typer(config_app, name="config")

# What each run status means on screen, and the colour it is printed in.
_STATUS_COLOURS = {
    RunStatus.SUCCESS: typer.colors.GREEN,
    RunStatus.PARTIAL: typer.colors.YELLOW,
    RunStatus.FAILED: typer.colors.RED,
    RunStatus.RUNNING: typer.colors.CYAN,
}


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
    days: int = typer.Option(3, "--days", min=0, max=90, help="How many days back to look."),
    limit: int = typer.Option(25, "--limit", min=1, max=250, help="How many notices to print."),
) -> None:
    """Call TED live and print what came back. Stores nothing.

    A read-only look at the real API, for checking that the configured query
    finds the kind of notice you expect.
    """
    settings = get_settings()
    configure_logging(settings.log_level, json_output=not settings.is_dev)

    config = _load_ted_config()

    typer.echo(f"Searching TED for the last {days} day(s). Nothing will be stored.")
    typer.echo("")

    printed = 0
    try:
        for row in ted_service.probe(config, days=days, limit=limit):
            printed += 1
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
    except Exception as exc:
        typer.secho(f"TED could not be read: {exc}", fg=typer.colors.RED)
        typer.echo("Nothing was stored. Try again, or run: watchdog config validate")
        raise typer.Exit(code=1) from exc

    typer.echo(f"{printed} notice(s). Nothing was stored.")


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
        _report_unmigrated_database()
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        typer.secho(f"That window cannot be read: {exc}", fg=typer.colors.RED)
        typer.echo("Nothing was changed.")
        raise typer.Exit(code=1) from exc

    _report_outcome(outcome)

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
        _report_unmigrated_database()
        raise typer.Exit(code=1) from exc

    if not history:
        typer.echo("No runs yet. Start one with: watchdog ingest --source ted")
        return

    for run in history:
        _print_run(run)


def _report_unmigrated_database() -> None:
    typer.secho("The database has not been set up yet.", fg=typer.colors.RED)
    typer.echo("Run this once, then try again:")
    typer.echo("  .\\tasks.ps1 migrate      (on Windows)")
    typer.echo("  make migrate             (on Linux or in a container)")


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
            typer.echo(f"  - {item.source_id}: {item.reason}")
        typer.echo("  They are still at the source and will be tried again next run.")

    if outcome.ok:
        typer.secho("Done.", fg=typer.colors.GREEN)
        if outcome.watermark_advanced:
            typer.echo(
                "The next run will start from "
                f"{outcome.window.window_to.strftime('%Y-%m-%d %H:%M')} UTC, less the overlap."
            )
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
