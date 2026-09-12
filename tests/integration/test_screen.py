"""`watchdog screen --stage 2` end to end, against a real database and a fake model."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from watchdog.core.enums import RunKind
from watchdog.core.models import Tender
from watchdog.core.settings import Settings
from watchdog.llm.disabled import DisabledProvider
from watchdog.llm.fake import FakeProvider
from watchdog.services import screen as screen_service
from watchdog.storage.repository import Repository

TenderFactory = Callable[..., Tender]


@pytest.fixture
def stocked(repository: Repository, make_tender: TenderFactory, run_id: str) -> Repository:
    repository.upsert_tenders(
        [make_tender(f"0000000{index}-2026") for index in range(1, 4)], run_id=run_id
    )
    return repository


def test_the_switched_off_path_assesses_nothing_and_records_no_run(
    stocked: Repository, tmp_path: Path
) -> None:
    outcome = screen_service.assess_sample(
        limit=5,
        settings=Settings(data_dir=tmp_path),
        provider=DisabledProvider(),
        repository=stocked,
    )

    assert outcome.ai_enabled is False
    assert outcome.run.results == ()
    assert outcome.run_id is None
    assert stocked.list_runs() == []


def test_a_sample_run_records_what_judged_it(stocked: Repository, tmp_path: Path) -> None:
    outcome = screen_service.assess_sample(
        limit=5,
        settings=Settings(data_dir=tmp_path),
        provider=FakeProvider(model="fake-1"),
        repository=stocked,
    )

    assert outcome.ai_enabled is True
    assert outcome.run.counts["assessed"] == 3

    run = stocked.list_runs()[0]
    assert run.kind is RunKind.SCREEN
    assert run.provider == "fake"
    assert run.model == "fake-1"
    assert run.prompt_version == "assess_v1"
    assert run.counts["assessed"] == 3
    assert run.tokens_in > 0


def test_a_sample_run_stores_no_screening_result(stocked: Repository, tmp_path: Path) -> None:
    screen_service.assess_sample(
        limit=5,
        settings=Settings(data_dir=tmp_path),
        provider=FakeProvider(),
        repository=stocked,
    )

    assert stocked.latest_screening_for(["ted:00000001-2026"]) == {}
