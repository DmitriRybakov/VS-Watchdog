"""Health endpoint. Stable JSON, no database and no network.

The only route reachable without signing in, which is what decides how little it
says. A monitor needs to know the process is answering. It does not need the
version, the environment or which model is configured, and every one of those is
worth having before attacking a site, so none of them is here any more. They are
all still visible inside the application, to somebody who has signed in.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "watchdog"}
