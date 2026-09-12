"""Health endpoint. Stable JSON, no database and no network."""

from __future__ import annotations

from fastapi import APIRouter

from watchdog import __version__
from watchdog.core.settings import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "service": "watchdog",
        "version": __version__,
        "environment": settings.environment,
        "llm_provider": settings.llm_provider,
    }
