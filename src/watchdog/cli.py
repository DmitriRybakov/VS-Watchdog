"""Command line entry point. Imports services and core only."""

from __future__ import annotations

import typer

from watchdog import __version__
from watchdog.core.countries import country_name, country_names
from watchdog.core.logging import configure_logging, get_logger
from watchdog.core.settings import get_settings
from watchdog.services import ted as ted_service

app = typer.Typer(help="Watchdog - tender screening for Entr Advisory & Decision Support.")
config_app = typer.Typer(help="Inspect and check the configuration.")
app.add_typer(config_app, name="config")


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


def _load_ted_config() -> ted_service.TedSourceConfig:
    try:
        return ted_service.get_config()
    except (OSError, ValueError) as exc:
        typer.secho(f"config/sources/ted.yaml could not be read: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    app()
