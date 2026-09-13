"""Ingesting a source into the register.

Every test here is about one of three promises: running twice is safe, a crash
loses nothing, and a notice is never dropped in silence. The source is a fake
that replays recorded TED payloads page by page, so the mapping under test is the
real one and no test touches the network.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from freezegun import freeze_time
from sqlalchemy import text
from tests.conftest import load_ted_fixture, ted_fixture_names

from watchdog.core.clock import utc_now
from watchdog.core.enums import RunKind, RunStatus, SourcePlatform
from watchdog.core.models import Tender
from watchdog.services.ingest import (
    DatabaseNotReady,
    IngestOutcome,
    IngestWindow,
    ingest,
    plan_window,
    recent_runs,
    retry_quarantined,
)
from watchdog.sources.base import RawNotice
from watchdog.sources.errors import MappingError, QueryError, TransportError
from watchdog.sources.ted.mapper import map_notice
from watchdog.storage.db import create_db_engine, create_session_factory
from watchdog.storage.repository import Repository
from watchdog.storage.tables import Base

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


@freeze_time(NOW)
def test_a_manual_window_cannot_advance_the_watermark_past_an_uncollected_gap(
    repository: Repository,
) -> None:
    # Three weeks without a run, then someone asks for the last seven days. The
    # run succeeds, but a fortnight was never collected, so moving the watermark
    # to the end of this window would skip those notices for good.
    three_weeks_ago = NOW - timedelta(days=21)
    repository.set_watermark(SourcePlatform.TED, three_weeks_ago)

    window = plan_window(
        repository.get_watermark(SourcePlatform.TED),
        now=NOW,
        since_days=7,
    )
    outcome = run_ingest(repository, FakeSource([[notice("900021-2026")]]), window=window)

    assert outcome.status is RunStatus.SUCCESS, "the run itself did what it was asked"
    assert outcome.counts.new == 1
    assert outcome.watermark_advanced is False

    watermark = repository.get_watermark(SourcePlatform.TED)
    assert watermark is not None
    assert watermark.last_successful_at == three_weeks_ago, "the gap is still outstanding"


@freeze_time(NOW)
def test_the_next_ordinary_run_still_collects_the_days_the_manual_window_skipped(
    repository: Repository,
) -> None:
    three_weeks_ago = NOW - timedelta(days=21)
    repository.set_watermark(SourcePlatform.TED, three_weeks_ago)

    manual = plan_window(repository.get_watermark(SourcePlatform.TED), now=NOW, since_days=7)
    run_ingest(repository, FakeSource([[notice("900022-2026")]]), window=manual)

    ordinary = plan_window(repository.get_watermark(SourcePlatform.TED), now=NOW)
    source = FakeSource([[notice("900023-2026")]])
    outcome = run_ingest(repository, source, window=ordinary)

    assert source.windows[0][0] == (three_weeks_ago - timedelta(hours=48)).date()
    assert outcome.watermark_advanced is True


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


@freeze_time(NOW)
def test_the_payload_of_a_quarantined_notice_is_kept_whole(repository: Repository) -> None:
    payload = unmappable("900033-2026")

    run_ingest(repository, FakeSource([[payload]]))

    stored = repository.list_quarantine()
    assert [item.source_id for item in stored] == ["900033-2026"]
    assert stored[0].payload == payload, "the mapper can only be re-run on the whole notice"
    assert stored[0].source is SourcePlatform.TED
    assert stored[0].error_type == "MappingError"
    assert stored[0].resolved is False


@freeze_time(NOW)
def test_a_quarantined_notice_names_the_run_that_fetched_it(repository: Repository) -> None:
    outcome = run_ingest(repository, FakeSource([[unmappable("900034-2026")]]))

    assert repository.list_quarantine()[0].run_id == outcome.run_id


@freeze_time(NOW)
def test_meeting_the_same_broken_notice_again_updates_rather_than_inserts(
    repository: Repository,
) -> None:
    run_ingest(repository, FakeSource([[unmappable("900035-2026")]]))

    with freeze_time(datetime(2026, 3, 3, 7, 0, tzinfo=UTC)):
        second = run_ingest(repository, FakeSource([[unmappable("900035-2026")]]))

    stored = repository.list_quarantine(unresolved_only=False, limit=100)
    assert len(stored) == 1
    assert stored[0].first_seen_at == NOW, "when we first met it does not move"
    assert stored[0].last_seen_at == datetime(2026, 3, 3, 7, 0, tzinfo=UTC)
    assert stored[0].run_id == second.run_id


@freeze_time(NOW)
def test_a_notice_that_fails_to_quarantine_holds_the_watermark_and_is_partial(
    repository: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A notice recorded in neither place is a silent loss, which is the one
    # outcome the watermark exists to prevent.
    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the quarantine table is not accepting writes")

    monkeypatch.setattr(Repository, "quarantine_notices", refuse)

    outcome = run_ingest(
        repository,
        FakeSource([[notice("900036-2026"), unmappable("900037-2026")]]),
    )

    assert outcome.status is RunStatus.PARTIAL
    assert outcome.counts.quarantined == 0
    assert outcome.counts.failed == 1
    assert outcome.counts.reconciles
    assert outcome.watermark_advanced is False
    assert repository.get_watermark(SourcePlatform.TED) is None
    assert any("would have been lost" in message for message in outcome.errors)


@freeze_time(NOW)
def test_a_notice_that_starts_mapping_correctly_stops_being_quarantined(
    repository: Repository,
) -> None:
    # The invariant is exactly one of two places, so a later run that reads it
    # closes the quarantine row rather than leaving it in both.
    run_ingest(repository, FakeSource([[unmappable("900038-2026")]]))
    assert len(repository.list_quarantine()) == 1

    run_ingest(repository, FakeSource([[notice("900038-2026")]]))

    assert repository.list_quarantine() == []
    assert repository.list_quarantine(unresolved_only=False)[0].resolved is True
    assert repository.get_tender(tender_id("900038-2026")) is not None


# --------------------------------------------------------------------- retry


@freeze_time(NOW)
def test_a_retry_recovers_a_notice_once_the_mapping_works(repository: Repository) -> None:
    run_ingest(repository, FakeSource([[unmappable("900040-2026")]]))
    quarantined = repository.list_quarantine()[0]

    # Standing in for a fixed mapper: the same payload, now readable.
    repository.quarantine_notices(
        [quarantined.model_copy(update={"payload": notice("900040-2026")})],
        run_id=quarantined.run_id,
    )

    outcome = retry_quarantined(repository=repository)

    assert outcome.status is RunStatus.SUCCESS
    assert outcome.counts["recovered"] == 1
    assert repository.get_tender(tender_id("900040-2026")) is not None
    assert repository.list_quarantine() == []


@freeze_time(NOW)
def test_a_retry_gets_its_own_run_and_never_moves_the_watermark(
    repository: Repository,
) -> None:
    run_ingest(repository, FakeSource([[unmappable("900041-2026")]]))
    watermark_before = repository.get_watermark(SourcePlatform.TED)

    outcome = retry_quarantined(repository=repository)

    run = repository.get_run(outcome.run_id)
    assert run is not None
    assert run.kind is RunKind.RETRY
    assert run.watermark_advanced is False
    assert run.window_from is None and run.window_to is None
    assert repository.get_watermark(SourcePlatform.TED) == watermark_before


@freeze_time(NOW)
def test_a_retry_keeps_the_original_failures_run_reference(repository: Repository) -> None:
    ingested = run_ingest(repository, FakeSource([[unmappable("900042-2026")]]))

    retry = retry_quarantined(repository=repository)

    still_there = repository.list_quarantine()[0]
    assert still_there.run_id == ingested.run_id
    assert still_there.run_id != retry.run_id


@freeze_time(NOW)
def test_a_notice_that_still_cannot_be_read_stays_quarantined_and_is_not_a_failure(
    repository: Repository,
) -> None:
    run_ingest(repository, FakeSource([[unmappable("900043-2026")]]))

    outcome = retry_quarantined(repository=repository)

    assert outcome.status is RunStatus.SUCCESS
    assert outcome.counts["still_failing"] == 1
    assert outcome.counts["recovered"] == 0
    assert repository.list_quarantine()[0].resolved is False


@freeze_time(NOW)
def test_a_retry_does_not_overwrite_newer_tender_data_with_an_older_payload(
    repository: Repository,
) -> None:
    # Quarantined on day one, read correctly on day three by a normal run. The
    # stored payload is now older than the register and must not win.
    run_ingest(repository, FakeSource([[unmappable("900044-2026")]]))
    repository.quarantine_notices(
        [
            repository.list_quarantine()[0].model_copy(
                update={"payload": notice("900044-2026", published="2026-02-01+01:00")}
            )
        ]
    )

    later = datetime(2026, 3, 5, 7, 0, tzinfo=UTC)
    with freeze_time(later):
        run_ingest(repository, FakeSource([[notice("900044-2026", published="2026-03-04+01:00")]]))

    # Named explicitly: the normal run already resolved it, which is the point.
    outcome = retry_quarantined(repository=repository, ids=[tender_id("900044-2026")])

    stored = repository.get_tender(tender_id("900044-2026"))
    assert stored is not None
    assert outcome.counts["already_current"] == 1
    assert outcome.counts["recovered"] == 0
    assert stored.published_date == date(2026, 3, 4), "the newer sighting stands"
    assert stored.last_seen_at == later


@freeze_time(NOW)
def test_a_failed_save_leaves_the_notice_unresolved(
    repository: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_ingest(repository, FakeSource([[unmappable("900045-2026")]]))
    quarantined = repository.list_quarantine()[0]
    repository.quarantine_notices(
        [quarantined.model_copy(update={"payload": notice("900045-2026")})],
        run_id=quarantined.run_id,
    )

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the database is not accepting writes")

    monkeypatch.setattr(Repository, "save_recovered_tender", refuse)

    outcome = retry_quarantined(repository=repository)

    assert outcome.status is RunStatus.FAILED
    assert outcome.counts["failed"] == 1
    assert repository.list_quarantine()[0].resolved is False
    assert repository.get_tender(tender_id("900045-2026")) is None


@freeze_time(NOW)
def test_a_retry_can_be_limited_to_named_notices(repository: Repository) -> None:
    run_ingest(
        repository,
        FakeSource([[unmappable("900046-2026"), unmappable("900047-2026")]]),
    )

    outcome = retry_quarantined(repository=repository, ids=[tender_id("900046-2026")])

    assert outcome.counts["attempted"] == 1
    assert [item.source_id for item in outcome.results] == ["900046-2026"]


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


@freeze_time(NOW)
def test_a_dry_run_still_reports_a_window_that_would_leave_a_gap(
    repository: Repository,
) -> None:
    # A dry run is when someone is checking whether a window is safe, so this is
    # exactly when the gap matters most.
    three_weeks_ago = NOW - timedelta(days=21)
    repository.set_watermark(SourcePlatform.TED, three_weeks_ago)

    window = plan_window(repository.get_watermark(SourcePlatform.TED), now=NOW, since_days=7)
    outcome = run_ingest(
        repository,
        FakeSource([[notice("900062-2026")]]),
        window=window,
        dry_run=True,
    )

    assert outcome.dry_run is True
    assert outcome.window.gap_from == three_weeks_ago
    assert outcome.window.leaves_no_gap is False


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
def test_a_database_error_never_carries_its_statement_or_the_notice_into_a_message(
    repository: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A SQLAlchemy error puts the whole failed statement and every bound value
    # after its first line, and here those values are the notice itself.
    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(
            "(sqlite3.OperationalError) table tender has no column named first_seen_run_id\n"
            "[SQL: INSERT INTO tender (id, source, title) VALUES (?, ?, ?)]\n"
            "[parameters: ('ted:900071-2026', 'ted', 'Hydrogen feasibility study')]"
        )

    monkeypatch.setattr(Repository, "upsert_tenders", refuse)

    outcome = run_ingest(repository, FakeSource([[notice("900071-2026")]]))

    reported = " ".join(outcome.errors)
    assert "no column named first_seen_run_id" in reported
    assert "INSERT INTO" not in reported
    assert "parameters" not in reported


# --------------------------------------------------- a projection TED refuses


@freeze_time(NOW)
def test_an_oversized_projection_fails_the_run_and_holds_the_watermark(
    repository: Repository,
) -> None:
    """TED prices a request as fields per page and refuses one over 10,000.

    The dangerous version of this is a run that reports success having stored
    nothing, so the watermark moves past a window nobody read. It has to be the
    loud kind of failure instead.
    """

    class RefusedSource(FakeSource):
        def discover(self, window_from: date, window_to: date) -> Iterator[RawNotice]:
            self.windows.append((window_from, window_to))
            raise QueryError(
                "TED rejected the request: Value (14250) of parameter 'Fields per page' "
                "exceeds maximum allowed value (10000)",
                field=None,
                query="(publication-date>=20260601)",
            )
            yield  # pragma: no cover - never reached, keeps this a generator

    outcome = run_ingest(repository, RefusedSource([[notice("900090-2026")]]))

    assert outcome.status is RunStatus.FAILED
    assert outcome.counts.fetched == 0
    assert outcome.watermark_advanced is False
    assert repository.get_watermark(SourcePlatform.TED) is None
    assert any("Fields per page" in message for message in outcome.errors)

    assert outcome.run_id is not None
    run = repository.get_run(outcome.run_id)
    assert run is not None
    assert run.status is RunStatus.FAILED
    assert run.watermark_advanced is False


@freeze_time(NOW)
def test_a_refused_projection_leaves_the_next_run_the_whole_window(
    repository: Repository,
) -> None:
    # The point of holding the watermark: once the page size is fixed, the run
    # that follows still covers the days the refused one never read.
    class RefusedSource(FakeSource):
        def discover(self, window_from: date, window_to: date) -> Iterator[RawNotice]:
            self.windows.append((window_from, window_to))
            raise QueryError("fields per page exceeded", field=None, query="q")
            yield  # pragma: no cover

    refused = RefusedSource([[]])
    run_ingest(repository, refused)

    recovered_source = FakeSource([[notice("900091-2026")]])
    recovered = run_ingest(repository, recovered_source)

    assert recovered.status is RunStatus.SUCCESS
    assert recovered.counts.new == 1
    assert recovered_source.windows == refused.windows, "the same days are asked for again"


@freeze_time(NOW)
def test_a_schema_one_migration_behind_is_a_sentence_not_a_statement() -> None:
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE tender DROP COLUMN first_seen_run_id"))
        store = Repository(create_session_factory(engine))

        with pytest.raises(DatabaseNotReady) as caught:
            recent_runs(repository=store)
    finally:
        engine.dispose()

    assert caught.value.check.missing_columns == ["tender.first_seen_run_id"]
    assert caught.value.check.empty is False
    assert "out of date" in str(caught.value)


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
