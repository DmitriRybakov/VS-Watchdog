"""Command line entry point. Imports services and core only."""

from __future__ import annotations

import typer

from watchdog import __version__
from watchdog.core.logging import configure_logging, get_logger
from watchdog.core.settings import get_settings

app = typer.Typer(help="Watchdog - tender screening for Entr Advisory & Decision Support.")


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@app.command()
def config() -> None:
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


if __name__ == "__main__":
    app()
