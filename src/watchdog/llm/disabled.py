"""The default provider: no model, no network, no cost.

This is what Watchdog ships with. Every screen must say that AI assessment is off
and every caller must produce a rules-only result when it meets
:class:`ProviderDisabled` - which is why that is a named type and not a generic
error. Nothing here is a stub to be filled in later: running with no model at all
is a supported way to use the tool.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from watchdog.llm.provider import DISABLED, ProviderDisabled, Usage


class DisabledProvider:
    """Refuses every call, by design and in one predictable way."""

    name: str = DISABLED
    model: str | None = None
    enabled: bool = False

    def assess[SchemaT: BaseModel](
        self,
        *,
        system: str,
        user: str,
        schema: type[SchemaT],
        check: Callable[[SchemaT], None] | None = None,
    ) -> tuple[SchemaT, Usage]:
        raise ProviderDisabled(
            "AI assessment is switched off, so this notice was screened on the "
            "deterministic rules alone"
        )
