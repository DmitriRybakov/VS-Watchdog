"""Ingesting a source into the register.

Every test here is about one of three promises: running twice is safe, a crash
loses nothing, and a notice is never dropped in silence. The source is a fake
that replays recorded TED payloads page by page, so the mapping under test is the
real one and no test touches the network.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from typing import Any

import pytest
from freezegun import freeze_time
from tests.conftest import load_ted_fixture, ted_fixture_names

from watchdog.core.clock import utc_now
from watchdog.core.enums import RunStatus, SourcePlatform
from watchdog.core.models import Tender
from watchdog.services.ingest import (
    DatabaseNotReady,
    IngestOutcome,
    IngestWindow,
    ingest,
    plan_window,
    recent_runs,
)
from watchdog.sources.base import RawNotice
from watchdog.sources.errors import MappingError, TransportError
from watchdog.sources.ted.mapper import map_notice
from watchdog.storage.db import create_db_engine, create_session_factory
from watchdog.storage.repository import Repository

NOW = datetime(2026, 3, 2, 7, 0, tzinfo=UTC)
BASE_NOTICE = "form_type_competition_is_a_contract_notice"

Payload = dict[str, Any]


class FakeSource:
    """Replays recorded payloads page by page, and can fail like TED does.

    It records the window it was asked for, because "did we ask for the right
    days" is a different question from "did we store what came back".
    """

    platform = SourcePlatform.TED

    def __init__(self, pages: Sequence[Sequence[Payload]], *, fail_after_page: int | None = None):
        self.pages = [list(page) for page in pages]
        self.fail_after_page = fail_after_page
        self.windows: list[tuple[date, date]] = []

    def discover(self, window_from: date, window_to: date) -> Iterator[RawNotice]:
        self.windows.append((window_from, window_to))

        for number, page in enumerate(self.pages, start=1):
            for payload in page:
                yield RawNotice.build(
                    self.platform,
                    str(payload["publication-number"]),
                    payload,
                    retrieved_at=utc_now(),
                )
            if number == self.fail_after_page:
                raise TransportError("TED stopped answering after 5 attempts", status=503)

    def to_tender(self, raw: RawNotice) -> Tender:
        return map_notice(
            raw.payload,
            source=self.platform,
            first_seen_at=raw.retrieved_at,
            last_seen_at=raw.retrieved_at,
        )


def notice(source_id: str, *, base: str = BASE_NOTICE, published: str | None = None) -> Payload:
    """A recorded notice under a new publication number, so identity is controlled."""
    payload = copy.deepcopy(load_ted_fixture(base))
    payload["publication-number"] = source_id
    if published is not None:
        payload["publication-date"] = published
    return payload


def unmappable(source_id: str) -> Payload:
    """A notice with an identity but no title of any kind: it cannot be mapped."""
    payload = notice(source_id)
    payload.pop("notice-title", None)
    payload.pop("title-proc", None)
    return payload


def tender_id(source_id: str) -> str:
    return f"{SourcePlatform.TED.value}:{source_id}"


def run_ingest(
    repository: Repository,
    source: FakeSource,
    *,
    window: IngestWindow | None = None,
    **kwargs: Any,
) -> IngestOutcome:
    return ingest(
        repository,
        SourcePlatform.TED,
        lambda _run_id: source,
        window=window or default_window(),
        **kwargs,
    )


def default_window() -> IngestWindow:
    return plan_window(None, now=NOW, backfill_days=30)


@pytest.fixture
def every_fixture() -> list[Payload]:
    return [load_ted_fixture(name) for name in ted_fixture_names()]


# ------------------------------------------------------------------- iteration


@freeze_time(NOW)
def test_iteration_returns_every_record_and_terminates(
    repository: Repository, every_fixture: list[Payload]
) -> None:
    source = FakeSource([every_fixture[:5], every_fixture[5:]])

    outcome = run_ingest(repository, source)

    assert outcome.status is RunStatus.SUCCESS
    assert outcome.counts.fetched == len(every_fixture)
    assert outcome.counts.new == len(every_fixture)
    assert outcome.counts.quarantined == 0
    assert outcome.counts.reconciles
    assert repository.list_tenders(limit=200).total == len(every_fixture)


@freeze_time(NOW)
def test_a_batch_is_written_as_it_arrives_rather_than_at_the_end(
    repository: Repository, every_fixture: list[Payload]
) -> None:
    seen: list[int] = []

    run_ingest(
        repository,
        FakeSource([every_fixture]),
        batch_size=5,
        on_progress=lambda counts: seen.append(counts.fetched),
    )

    assert len(seen) > 1, "a twelve-notice run in batches of five should report more than once"


# ------------------------------------------------------------ crash and resume


@freeze_time(NOW)
def test_a_failure_after_the_first_page_is_partial_and_holds_the_watermark(
    repository: Repository,
) -> None:
    first = [notice("900001-2026"), notice("900002-2026")]
    second = [notice("900003-2026")]

    outcome = run_ingest(repository, FakeSource([first, second], fail_after_page=1))

    assert outcome.status is RunStatus.PARTIAL
    assert outcome.counts.new == 2, "what arrived before the failure is kept"
    assert outcome.watermark_advanced is False
    assert repository.get_watermark(SourcePlatform.TED) is None

    assert outcome.run_id is not None
    run = repository.get_run(outcome.run_id)
    assert run is not None
    assert run.status is RunStatus.PARTIAL
    assert run.watermark_advanced is False
    assert any("could not be read to the end" in message for message in run.errors)


@freeze_time(NOW)
def test_rerunning_after_a_partial_run_converges_with_no_duplicates(
    repository: Repository,
) -> None:
    first = [notice("900001-2026"), notice("900002-2026")]
    second = [notice("900003-2026")]

    partial = run_ingest(repository, FakeSource([first, second], fail_after_page=1))
    recovered = run_ingest(repository, FakeSource([first, second]))

    assert partial.status is RunStatus.PARTIAL
    assert recovered.status is RunStatus.SUCCESS
    assert recovered.counts.new == 1, "only the notice the partial run never reached is new"
    assert recovered.counts.unchanged == 2
    assert repository.list_tenders(limit=200).total == 3
    assert recovered.watermark_advanced is True


@freeze_time(NOW)
def test_a_failure_before_anything_was_stored_is_failed_not_partial(
    repository: Repository,
) -> None:
    outcome = run_ingest(repository, FakeSource([[]], fail_after_page=1))

    assert outcome.status is RunStatus.FAILED
    assert repository.get_watermark(SourcePlatform.TED) is None


# ---------------------------------------------------------------- the overlap


@freeze_time(NOW)
def test_the_forty_eight_hour_overlap_asks_for_the_boundary_day(repository: Repository) -> None:
    # Half past midnight UTC: the window must reach back to 27 February, not 28.
    repository.set_watermark(SourcePlatform.TED, datetime(2026, 3, 1, 0, 30, tzinfo=UTC))
    window = plan_window(
        repository.get_watermark(SourcePlatform.TED),
        now=datetime(2026, 3, 1, 6, 0, tzinfo=UTC),
        overlap_hours=48,
    )

    boundary = notice("900010-2026", published="2026-02-27+01:00")
    source = FakeSource([[boundary]])

    outcome = run_ingest(repository, source, window=window)

    assert source.windows == [(date(2026, 2, 27), date(2026, 3, 1))]
    assert outcome.counts.new == 1
    stored = repository.get_tender(tender_id("900010-2026"))
    assert stored is not None
    assert stored.published_date == date(2026, 2, 27)


@freeze_time(NOW)
def test_a_correction_arriving_inside_the_overlap_is_an_update_not_a_duplicate(
    repository: Repository,
) -> None:
    original = notice("900011-2026")
    run_ingest(repository, FakeSource([[original]]))

    corrected = copy.deepcopy(original)
    corrected["change-notice-version-identifier"] = "02"

    outcome = run_ingest(repository, FakeSource([[corrected]]))

    assert outcome.counts.new == 0
    assert outcome.counts.updated == 1
    assert repository.list_tenders(limit=200).total == 1


# ----------------------------------------------------------------- idempotence


@freeze_time(NOW)
def test_a_second_identical_run_creates_no_rows_and_no_change_records(
    repository: Repository, every_fixture: list[Payload]
) -> None:
    run_ingest(repository, FakeSource([every_fixture]))

    outcome = run_ingest(repository, FakeSource([every_fixture]))

    assert outcome.counts.new == 0
    assert outcome.counts.updated == 0
    assert outcome.counts.unchanged == len(every_fixture)
    assert repository.list_tenders(limit=200).total == len(every_fixture)

    for payload in every_fixture:
        assert repository.list_changes(tender_id(str(payload["publication-number"]))) == []


@freeze_time(NOW)
def test_the_watermark_moves_to_the_end_of_the_window_only_on_success(
    repository: Repository,
) -> None:
    window = default_window()

    outcome = run_ingest(repository, FakeSource([[notice("900020-2026")]]), window=window)

    watermark = repository.get_watermark(SourcePlatform.TED)
    assert outcome.watermark_advanced is True
    assert watermark is not None
    assert watermark.last_successful_at == window.window_to


# ------------------------------------------------------------------ quarantine


@freeze_time(NOW)
def test_one_malformed_record_is_quarantined_and_the_rest_persist(
    repository: Repository,
) -> None:
    page = [notice("900030-2026"), unmappable("900031-2026"), notice("900032-2026")]

    outcome = run_ingest(repository, FakeSource([page]))

    assert outcome.status is RunStatus.SUCCESS
    assert outcome.counts.fetched == 3
    assert outcome.counts.new == 2
    assert outcome.counts.quarantined == 1
    assert outcome.counts.reconciles

    assert [item.source_id for item in outcome.quarantined] == ["900031-2026"]
    assert repository.get_tender(tender_id("900030-2026")) is not None
    assert repository.get_tender(tender_id("900032-2026")) is not None
    assert repository.get_tender(tender_id("900031-2026")) is None


@freeze_time(NOW)
def test_the_quarantined_publication_number_and_reason_are_recorded_on_the_run(
    repository: Repository,
) -> None:
    outcome = run_ingest(repository, FakeSource([[unmappable("900031-2026")]]))

    assert outcome.run_id is not None
    run = repository.get_run(outcome.run_id)
    assert run is not None
    assert run.counts["quarantined"] == 1
    assert any("900031-2026" in message for message in run.errors)


@freeze_time(NOW)
def test_a_quarantined_notice_does_not_hold_the_watermark_back(repository: Repository) -> None:
    # It will be just as unmappable tomorrow; holding the window open for it
    # would stop the source ever advancing.
    outcome = run_ingest(repository, FakeSource([[unmappable("900031-2026")]]))

    assert outcome.watermark_advanced is True


# --------------------------------------------------------- nothing is deleted


@freeze_time(NOW)
def test_a_tender_that_stops_appearing_in_the_source_is_not_deleted(
    repository: Repository,
) -> None:
    staying = notice("900040-2026")
    vanishing = notice("900041-2026")

    first = run_ingest(repository, FakeSource([[staying, vanishing]]))

    with freeze_time(datetime(2026, 3, 3, 7, 0, tzinfo=UTC)):
        run_ingest(repository, FakeSource([[staying]]))

    gone = repository.get_tender(tender_id("900041-2026"))
    assert gone is not None
    assert gone.last_seen_at == NOW, "it was not seen again, so its last sighting stands"
    assert gone.last_seen_run_id == first.run_id


# ---------------------------------------------------------------- traceability


@freeze_time(NOW)
def test_every_stored_tender_names_the_run_that_fetched_it(repository: Repository) -> None:
    first = run_ingest(repository, FakeSource([[notice("900050-2026")]]))

    stored = repository.get_tender(tender_id("900050-2026"))
    assert stored is not None
    assert stored.first_seen_run_id == first.run_id
    assert stored.last_seen_run_id == first.run_id
    assert repository.get_run(first.run_id or "") is not None


@freeze_time(NOW)
def test_the_first_run_is_remembered_and_the_latest_sighting_moves(
    repository: Repository,
) -> None:
    first = run_ingest(repository, FakeSource([[notice("900051-2026")]]))
    second = run_ingest(repository, FakeSource([[notice("900051-2026")]]))

    stored = repository.get_tender(tender_id("900051-2026"))
    assert stored is not None
    assert stored.first_seen_run_id == first.run_id
    assert stored.last_seen_run_id == second.run_id
    assert first.run_id != second.run_id


# -------------------------------------------------------------------- dry run


@freeze_time(NOW)
def test_a_dry_run_reports_what_would_change_and_stores_nothing(
    repository: Repository,
) -> None:
    run_ingest(repository, FakeSource([[notice("900060-2026")]]))
    runs_before = len(repository.list_runs(limit=50))
    watermark_before = repository.get_watermark(SourcePlatform.TED)

    outcome = run_ingest(
        repository,
        FakeSource([[notice("900060-2026"), notice("900061-2026")]]),
        dry_run=True,
    )

    assert outcome.dry_run is True
    assert outcome.run_id is None
    assert outcome.counts.new == 1
    assert outcome.counts.updated == 1
    assert outcome.watermark_advanced is False

    assert repository.get_tender(tender_id("900061-2026")) is None
    assert len(repository.list_runs(limit=50)) == runs_before
    assert repository.get_watermark(SourcePlatform.TED) == watermark_before


# ------------------------------------------------------------- write failures


@freeze_time(NOW)
def test_a_write_that_fails_stops_the_run_and_holds_the_watermark(
    repository: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the database is not accepting writes")

    monkeypatch.setattr(Repository, "upsert_tenders", refuse)

    outcome = run_ingest(repository, FakeSource([[notice("900070-2026")]]))

    assert outcome.status is RunStatus.FAILED
    assert outcome.counts.failed == 1
    assert outcome.counts.reconciles
    assert outcome.watermark_advanced is False
    assert any("could not be written" in message for message in outcome.errors)


@freeze_time(NOW)
def test_a_mapping_failure_carries_the_publication_number_it_belongs_to() -> None:
    with pytest.raises(MappingError) as caught:
        map_notice(unmappable("900080-2026"))

    assert caught.value.source_id == "900080-2026"


def test_an_unmigrated_database_is_a_named_condition_not_a_database_error() -> None:
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    try:
        store = Repository(create_session_factory(engine))
        with pytest.raises(DatabaseNotReady):
            recent_runs(repository=store)
    finally:
        engine.dispose()
