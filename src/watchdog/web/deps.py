"""What a route is given. One place, so a test can replace any of it.

Routes take these through ``Depends`` rather than reaching for a module-level
factory, which is what lets a test point the whole application at an in-memory
database without touching an environment variable.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from watchdog.core.settings import Settings, get_settings
from watchdog.storage.db import get_session_factory
from watchdog.storage.repository import Repository


def get_repository() -> Repository:
    """The application's queries, over the configured database."""
    return Repository(get_session_factory())


Store = Annotated[Repository, Depends(get_repository)]
Config = Annotated[Settings, Depends(get_settings)]
