"""The interface every source implements, and the raw notice it hands over.

Discovery and mapping are deliberately separate. ``discover`` talks to the
network and returns payloads exactly as received; ``to_tender`` is pure and can
be run again over a stored payload years later without the source being awake.
That is what makes a re-map possible when a field turns out to have been read
wrongly.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from watchdog.core.clock import utc_now
from watchdog.core.enums import SourcePlatform
from watchdog.core.models import Tender, UtcDatetime


def payload_hash(payload: dict[str, Any]) -> str:
    """A stable fingerprint of a payload, so an unchanged notice is recognised.

    Keys are sorted, so the same facts in a different order hash the same. This
    is the *payload* hash: whether the source re-sent identical bytes. It is a
    different question from ``Tender.content_hash``, which asks whether the text
    a screening read has changed.
    """
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class RawNotice(BaseModel):
    """One notice exactly as the source sent it, with when we fetched it."""

    model_config = ConfigDict(from_attributes=True)

    source: SourcePlatform
    source_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    retrieved_at: UtcDatetime = Field(default_factory=utc_now)
    content_hash: str = ""

    @classmethod
    def build(
        cls,
        source: SourcePlatform,
        source_id: str,
        payload: dict[str, Any],
        *,
        retrieved_at: datetime | None = None,
    ) -> RawNotice:
        return cls(
            source=source,
            source_id=source_id,
            payload=payload,
            retrieved_at=retrieved_at or utc_now(),
            content_hash=payload_hash(payload),
        )


@runtime_checkable
class TenderSource(Protocol):
    """What a source adapter has to provide. TED first, Doffin and the banks later."""

    platform: SourcePlatform

    def discover(self, window_from: date, window_to: date) -> Iterator[RawNotice]:
        """Every notice published in the window, as received.

        The window is in calendar dates because that is the precision the
        publication date has. Raises ``TransportError`` or ``InvalidPayloadError``
        rather than returning a short list: a partial window must never look like
        a complete one.
        """
        ...

    def to_tender(self, raw: RawNotice) -> Tender:
        """Map one payload onto the canonical model.

        Pure: no network, no clock beyond the notice's own timestamps. Raises
        ``MappingError`` carrying the identifier rather than returning something
        half-built.
        """
        ...
