from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from watchdog.core.enums import ContractNature, NoticeStage, SourcePlatform
from watchdog.core.models import Tender
from watchdog.storage.db import SessionFactory, create_db_engine, create_session_factory
from watchdog.storage.repository import Repository
from watchdog.storage.tables import Base

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def client() -> Iterator[TestClient]:
    from watchdog.web.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def session_factory() -> Iterator[SessionFactory]:
    """An empty database in memory, built from the same metadata as production."""
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


@pytest.fixture
def repository(session_factory: SessionFactory) -> Repository:
    return Repository(session_factory)


TenderFactory = Callable[..., Tender]


@pytest.fixture
def make_tender() -> TenderFactory:
    """Build a plausible tender. Anything else a test needs, set with ``model_copy``."""
    return _make_tender


def _make_tender(
    source_id: str = "00123456-2026",
    *,
    source: SourcePlatform = SourcePlatform.TED,
    title: str = "Feasibility study for a hydrogen production facility",
    description: str | None = "Pre-FEED study including techno-economic analysis.",
    buyer_country: str | None = "NO",
    published_date: date | None = date(2026, 3, 1),
    deadline: datetime | None = datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
    notice_stage: NoticeStage = NoticeStage.CONTRACT_NOTICE,
    source_version: str | None = "1",
    cpv_main: str | None = "71241000",
    seen_at: datetime = datetime(2026, 3, 2, 6, 0, tzinfo=UTC),
) -> Tender:
    return Tender(
        source=source,
        source_id=source_id,
        source_version=source_version,
        source_url=f"https://ted.europa.eu/notice/{source_id}",
        title=title,
        title_language="EN",
        description=description,
        buyer_name="Statsbygg",
        buyer_country=buyer_country,
        place_of_performance="Oslo",
        published_date=published_date,
        deadline=deadline,
        notice_stage=notice_stage,
        notice_subtype="16",
        contract_nature=ContractNature.SERVICES,
        cpv_main=cpv_main,
        cpv_additional=["73210000"],
        estimated_value=Decimal("250000.00"),
        currency="NOK",
        documents_url=f"https://ted.europa.eu/notice/{source_id}/documents",
        languages=["EN", "NO"],
        multi_lot=False,
        first_seen_at=seen_at,
        last_seen_at=seen_at,
        raw={"notice-id": source_id},
    )
