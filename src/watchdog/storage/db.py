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

# Recycle a pooled connection before a managed pooler drops it. Supabase closes
# idle server connections, and a host that sleeps wakes up holding a pool full of
# sockets the other end has already forgotten.
POOL_RECYCLE_SECONDS = 240

# Small on purpose: a free instance is one worker, and a pooled connection that
# nobody uses is still a connection the pooler counts.
POOL_SIZE = 5
MAX_OVERFLOW = 2


def normalise_database_url(url: str) -> str:
    """The URL a provider hands out, in the form this application can actually use.

    Two corrections, both of which are silent failures if left undone:

    - **The driver.** SQLAlchemy reads a bare ``postgresql://`` as psycopg2, which
      is not installed here. Supabase, Render and Heroku all print the bare form.
    - **Encrypted transport.** libpq defaults to ``prefer``, which quietly accepts
      an unencrypted connection if the server offers one. Every notice, review and
      credential would then cross the internet in clear text, and nothing on
      screen would say so.
    """
    if url.startswith("postgres://"):
        url = f"postgresql://{url.removeprefix('postgres://')}"
    if url.startswith("postgresql://"):
        url = f"postgresql+psycopg://{url.removeprefix('postgresql://')}"

    if url.startswith("postgresql") and "sslmode=" not in url:
        url += "&sslmode=require" if "?" in url else "?sslmode=require"

    return url


def create_db_engine(url: str, *, echo: bool = False) -> Engine:
    """Build an engine for a SQLite file, an in-memory SQLite database or PostgreSQL."""
    url = normalise_database_url(url)
    options: dict[str, Any] = {"echo": echo, "future": True}

    if url.startswith("sqlite"):
        # The web process serves requests on several threads; SQLite objects to
        # that unless told otherwise.
        options["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url.endswith("sqlite://"):
            # One shared connection, otherwise each session gets an empty database.
            options["poolclass"] = StaticPool
    else:
        # A connection that was fine before the service went to sleep is not fine
        # after it. Checking costs one round trip; not checking costs the request.
        options["pool_pre_ping"] = True
        options["pool_recycle"] = POOL_RECYCLE_SECONDS
        options["pool_size"] = POOL_SIZE
        options["max_overflow"] = MAX_OVERFLOW

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
