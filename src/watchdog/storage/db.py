"""Engine and session setup.

Nothing here runs at import time: the engine is built when ``get_session_factory()``
is first called, from DATABASE_URL. Tests build their own factory against an
in-memory database and pass it to the repository.

Tables are never created here. Schema changes are an explicit migration
(``make migrate`` / ``.\\tasks.ps1 migrate``).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from watchdog.core.settings import get_settings

SessionFactory = sessionmaker[Session]


def create_db_engine(url: str, *, echo: bool = False) -> Engine:
    """Build an engine for a SQLite file, an in-memory SQLite database or PostgreSQL."""
    options: dict[str, Any] = {"echo": echo, "future": True}

    if url.startswith("sqlite"):
        # The web process serves requests on several threads; SQLite objects to
        # that unless told otherwise.
        options["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url.endswith("sqlite://"):
            # One shared connection, otherwise each session gets an empty database.
            options["poolclass"] = StaticPool

    engine = create_engine(url, **options)

    if engine.dialect.name == "sqlite":
        _enforce_sqlite_foreign_keys(engine)

    return engine


def create_session_factory(engine: Engine) -> SessionFactory:
    """A session factory whose objects stay readable after commit."""
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@lru_cache(maxsize=1)
def get_session_factory() -> SessionFactory:
    """The process-wide session factory, built once from settings."""
    return create_session_factory(create_db_engine(get_settings().database_url))


def _enforce_sqlite_foreign_keys(engine: Engine) -> None:
    """SQLite ignores foreign keys unless each connection asks for them."""

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
