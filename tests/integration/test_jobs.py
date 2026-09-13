"""The lock, the progress row and the recovery at startup.

None of these call TED or a model: the work itself is stubbed, because what is
being tested is that two runs cannot overlap and that a run which died is visible
rather than permanently in progress.

The clock is moved rather than waited on. "This run is dead" is a claim about
silence, so the tests that make it have to produce the silence.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from freezegun import freeze_time

from watchdog.core.clock import utc_now
from watchdog.core.enums import RunKind, RunStatus
from watchdog.core.models import STALE_JOB_AFTER
from watchdog.services import jobs
from watchdog.services.ingest import DatabaseNotReady
from watchdog.services.query import PIPELINE_JOB
from watchdog.storage.db import create_db_engine, create_session_factory
from watchdog.storage.repository import Repository


def test_a_second_run_cannot_start_while_one_is_active(repository: Repository) -> None:
    first = jobs.claim(jobs.UPDATE, repository=repository)

    assert first.state.held is True

    try:
        jobs.claim(jobs.RESCREEN, repository=repository)
    except jobs.JobBusy as exc:
        assert "already in progress" in str(exc)
    else:  # pragma: no cover - the lock failed to hold
        raise AssertionError("a second run was allowed to start")


def test_releasing_the_lock_lets_the_next_run_start(repository: Repository) -> None:
    jobs.claim(jobs.UPDATE, repository=repository)
    repository.release_job(PIPELINE_JOB, status=RunStatus.SUCCESS, counts={"new": 3})

    second = jobs.claim(jobs.RESCREEN, repository=repository)

    assert second.state.held is True


def test_progress_is_readable_while_the_job_holds_the_lock(repository: Repository) -> None:
    jobs.claim(jobs.UPDATE, repository=repository)
    repository.update_job(PIPELINE_JOB, phase="Reading notices from TED", processed=40, total=200)

    state = jobs.status(repository=repository)

    assert state is not None
    assert state.phase == "Reading notices from TED"
    assert state.percent == 20


def test_an_unknown_total_is_reported_as_unknown_rather_than_zero_percent(
    repository: Repository,
) -> None:
    jobs.claim(jobs.UPDATE, repository=repository)
    repository.update_job(PIPELINE_JOB, processed=12, total=0)

    state = jobs.status(repository=repository)

    assert state is not None
    assert state.percent is None


def test_a_run_left_running_by_a_dead_process_is_marked_interrupted(
    repository: Repository,
) -> None:
    stranded = repository.start_run(RunKind.INGEST)
    jobs.claim(jobs.UPDATE, repository=repository)

    # The process is killed here and a new one starts later. Nothing has reported
    # progress in the meantime, which is how the new one can tell.
    with freeze_time(utc_now() + STALE_JOB_AFTER + timedelta(minutes=1)):
        recovered = jobs.recover(repository=repository)

    assert stranded.run_id in recovered
    run = repository.get_run(stranded.run_id)
    assert run is not None
    assert run.status is RunStatus.INTERRUPTED
    assert run.finished_at is not None


def test_a_restart_leaves_a_run_that_is_still_reporting_alone(repository: Repository) -> None:
    """The case a host that sleeps creates: two instances alive at the same time.

    A deploy starts a new instance while the old one is still working. Clearing
    the lock here would let a second run start on top of the first, and both would
    write to the same register.
    """
    live = repository.start_run(RunKind.INGEST)
    jobs.claim(jobs.UPDATE, repository=repository)
    repository.update_job(PIPELINE_JOB, phase="Reading notices from TED", processed=300)

    assert jobs.recover(repository=repository) == []

    state = jobs.status(repository=repository)
    assert state is not None
    assert state.held is True
    assert state.running is True
    run = repository.get_run(live.run_id)
    assert run is not None
    assert run.status is RunStatus.RUNNING


def test_a_run_that_stopped_reporting_can_be_taken_over_without_a_restart(
    repository: Repository,
) -> None:
    """A killed run must not disable the buttons until somebody restarts the service."""
    jobs.claim(jobs.UPDATE, repository=repository)

    with freeze_time(utc_now() + STALE_JOB_AFTER + timedelta(minutes=1)):
        stale = jobs.status(repository=repository)
        assert stale is not None
        assert stale.stale is True
        assert stale.running is False

        assert jobs.claim(jobs.RESCREEN, repository=repository).state.held is True


def test_recovery_frees_the_lock_so_the_buttons_work_again(repository: Repository) -> None:
    jobs.claim(jobs.UPDATE, repository=repository)

    with freeze_time(utc_now() + STALE_JOB_AFTER + timedelta(minutes=1)):
        jobs.recover(repository=repository)

        state = jobs.status(repository=repository)
        assert state is not None
        assert state.held is False
        assert state.status is RunStatus.INTERRUPTED
        assert jobs.claim(jobs.UPDATE, repository=repository).state.held is True


def test_an_interrupted_run_is_not_reported_as_a_failure(repository: Repository) -> None:
    """Nothing raised, so what it wrote is unknown rather than wrong."""
    repository.start_run(RunKind.SCREEN)

    with freeze_time(utc_now() + STALE_JOB_AFTER + timedelta(minutes=1)):
        jobs.recover(repository=repository)

    state = jobs.status(repository=repository)
    assert state is None or state.status is not RunStatus.FAILED


def test_a_failing_job_records_one_sentence_and_releases_the_lock(
    repository: Repository, monkeypatch
) -> None:
    def explode(**_: object) -> None:
        raise RuntimeError("TED returned 503\nwith a very long body nobody should read")

    monkeypatch.setattr(jobs, "ingest_ted", explode)
    jobs.claim(jobs.UPDATE, repository=repository)

    jobs.run(jobs.UPDATE, repository=repository)

    state = jobs.status(repository=repository)
    assert state is not None
    assert state.held is False
    assert state.status is RunStatus.FAILED
    assert state.error == "TED returned 503"
    assert "Traceback" not in (state.error or "")


def test_the_button_refuses_a_second_press_rather_than_queueing_one(
    client: TestClient, repository: Repository
) -> None:
    jobs.claim(jobs.UPDATE, repository=repository)

    response = client.post("/runs/rescreen")

    assert response.status_code == 200
    assert "already in progress" in response.text


def test_the_status_endpoint_disables_the_buttons_while_a_run_is_going(
    client: TestClient, repository: Repository
) -> None:
    jobs.claim(jobs.UPDATE, repository=repository)

    html = client.get("/runs/status").text

    assert "disabled" in html
    assert 'hx-trigger="every 1s"' in html


def test_the_runs_page_lists_a_run_with_its_counts_and_status(
    client: TestClient, repository: Repository
) -> None:
    started = repository.start_run(RunKind.INGEST)
    repository.finish_run(
        started.run_id,
        status=RunStatus.PARTIAL,
        counts={"new": 12, "updated": 3},
        errors=["one page of the window failed"],
    )

    html = client.get("/runs").text

    assert "partial" in html
    assert "new 12" in html
    assert "one page of the window failed" in html


def test_an_empty_runs_page_says_what_to_press(client: TestClient) -> None:
    html = client.get("/runs").text

    assert "No runs yet" in html
    assert "Update from TED" in html


# --------------------------------------------- the terminal and the browser
#
# One lock, both interfaces. During a deployment a backfill runs from Windows
# against the hosted database for an hour while the site is live, which is
# exactly when somebody presses Update. Both runs would survive it - a notice
# keeps its row - but each would report counts for the other's work.


def test_a_command_line_run_stops_the_button(repository: Repository) -> None:
    with jobs.hold(jobs.CLI_INGEST, repository=repository) as held:
        held.report("reading notices from TED", processed=120, total=400)

        with pytest.raises(jobs.JobBusy) as refused:
            jobs.claim(jobs.UPDATE, repository=repository)

    assert "already in progress" in str(refused.value)
    # Named, so the person at the button knows it is not theirs to wait for.
    assert "Ingest (command line)" in str(refused.value)
    assert "last reported" in str(refused.value)


def test_the_button_stops_a_command_line_run(repository: Repository) -> None:
    jobs.claim(jobs.UPDATE, repository=repository)

    with pytest.raises(jobs.JobBusy) as refused:  # noqa: SIM117 - two contexts, two subjects
        with jobs.hold(jobs.CLI_SCREEN, repository=repository):
            raise AssertionError("the command should not have got the lock")

    assert "Update from TED" in str(refused.value)


def test_the_page_says_what_is_running_when_it_is_a_terminal(
    client: TestClient, repository: Repository
) -> None:
    with jobs.hold(jobs.CLI_INGEST, repository=repository) as held:
        held.report("reading notices from TED", processed=120, total=400)

        html = client.get("/runs/status").text

    assert "Ingest (command line)" in html
    assert "disabled" in html


def test_a_command_releases_the_lock_when_it_finishes(repository: Repository) -> None:
    with jobs.hold(jobs.CLI_INGEST, repository=repository) as held:
        held.finished(counts={"new": 7}, status=RunStatus.SUCCESS)

    state = jobs.status(repository=repository)
    assert state is not None
    assert state.held is False
    assert state.status is RunStatus.SUCCESS
    assert state.counts == {"new": 7}
    assert jobs.claim(jobs.UPDATE, repository=repository).state.held is True


def test_a_command_that_fails_releases_the_lock_with_one_sentence(repository: Repository) -> None:
    with pytest.raises(RuntimeError):  # noqa: SIM117 - two contexts, two subjects
        with jobs.hold(jobs.CLI_INGEST, repository=repository):
            raise RuntimeError("TED returned 503\nwith a body nobody should read")

    state = jobs.status(repository=repository)
    assert state is not None
    assert state.held is False
    assert state.status is RunStatus.FAILED
    assert state.error == "TED returned 503"


def test_ctrl_c_hands_the_lock_back_rather_than_leaving_it_to_time_out(
    repository: Repository,
) -> None:
    """A command that knows it is stopping should not disable the buttons for fifteen minutes."""
    with pytest.raises(KeyboardInterrupt):  # noqa: SIM117 - two contexts, two subjects
        with jobs.hold(jobs.CLI_INGEST, repository=repository):
            raise KeyboardInterrupt

    state = jobs.status(repository=repository)
    assert state is not None
    assert state.held is False
    assert state.status is RunStatus.INTERRUPTED


def test_a_long_command_that_keeps_reporting_is_not_treated_as_dead(
    repository: Repository,
) -> None:
    """The takeover rule reads silence, not elapsed time. A backfill takes hours."""
    later = utc_now() + STALE_JOB_AFTER + timedelta(minutes=1)

    with jobs.hold(jobs.CLI_INGEST, repository=repository) as held, freeze_time(later):
        held.report("reading notices from TED", processed=9000)

        assert jobs.recover(repository=repository) == []
        with pytest.raises(jobs.JobBusy):
            jobs.claim(jobs.UPDATE, repository=repository)


def test_a_command_line_run_that_was_killed_is_taken_over_like_any_other(
    repository: Repository,
) -> None:
    jobs.claim(jobs.CLI_INGEST, repository=repository)

    with freeze_time(utc_now() + STALE_JOB_AFTER + timedelta(minutes=1)):
        assert jobs.claim(jobs.UPDATE, repository=repository).state.held is True


def test_a_database_with_no_tables_is_named_rather_than_failing_on_the_lock() -> None:
    """Taking a lock in a database that has none would be a SQL error, not an answer."""
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    empty = Repository(create_session_factory(engine))

    try:
        with pytest.raises(DatabaseNotReady) as refused:  # noqa: SIM117 - two contexts, two subjects
            with jobs.hold(jobs.CLI_INGEST, repository=empty):
                raise AssertionError("the command should not have got that far")
    finally:
        engine.dispose()

    assert "no tables" in str(refused.value)
