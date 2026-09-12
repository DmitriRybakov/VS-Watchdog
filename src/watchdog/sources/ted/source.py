"""The TED adapter: the two methods every source implements.

``discover`` and ``to_tender`` stay separate so that a stored payload can be
re-mapped later, without TED being awake, when a field turns out to have been
read wrongly.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

from watchdog.core.clock import utc_now
from watchdog.core.enums import SourcePlatform
from watchdog.core.models import Tender
from watchdog.sources.base import RawNotice
from watchdog.sources.errors import MappingError
from watchdog.sources.ted.client import TedClient
from watchdog.sources.ted.config import TedSourceConfig
from watchdog.sources.ted.mapper import REQUESTED_FIELDS, map_notice
from watchdog.sources.ted.query import QueryProfile, build_query


class TedSource:
    """Reads TED. Satisfies the TenderSource protocol."""

    platform = SourcePlatform.TED

    def __init__(
        self,
        client: TedClient,
        config: TedSourceConfig,
        *,
        profile: QueryProfile = QueryProfile.DEFAULT,
        run_id: str | None = None,
    ) -> None:
        self._client = client
        self._config = config
        self._profile = profile
        self._run_id = run_id

    def query_for(self, window_from: date, window_to: date) -> str:
        return build_query(self._config, window_from, window_to, profile=self._profile)

    def discover(self, window_from: date, window_to: date) -> Iterator[RawNotice]:
        """Every notice published in the window, as received.

        The query is validated before the first page, so a mistake in the
        configuration fails immediately and by name instead of returning nothing.
        """
        query = self.query_for(window_from, window_to)
        self._client.validate_query(query, REQUESTED_FIELDS, run_id=self._run_id)

        for payload in self._client.iter_notices(
            query,
            REQUESTED_FIELDS,
            run_id=self._run_id,
            page_size=self._config.page_size,
            only_latest_versions=True,
        ):
            source_id = payload.get("publication-number")
            if isinstance(source_id, list):
                source_id = source_id[0] if source_id else None
            if not isinstance(source_id, str) or not source_id:
                raise MappingError("a notice arrived without a publication-number")

            yield RawNotice.build(self.platform, source_id, payload, retrieved_at=utc_now())

    def to_tender(self, raw: RawNotice) -> Tender:
        return map_notice(
            raw.payload,
            source=self.platform,
            first_seen_at=raw.retrieved_at,
            last_seen_at=raw.retrieved_at,
        )
