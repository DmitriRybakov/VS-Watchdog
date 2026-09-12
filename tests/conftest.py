from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from watchdog.core.enums import ContractNature, DeadlineType, NoticeStage, SourcePlatform
from watchdog.core.models import Tender, TextBlock
from watchdog.screening import RuleEngine, RulesConfig, load_rules_config
from watchdog.storage.db import SessionFactory, create_db_engine, create_session_factory
from watchdog.storage.repository import Repository
from watchdog.storage.tables import Base

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TED_FIXTURES_DIR = FIXTURES_DIR / "ted"
# The shipped configuration, found from the repository root rather than the
# working directory, so a test run from anywhere reads the same files.
CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def load_ted_fixture(name: str) -> dict:
    """One recorded TED notice. Named for the thing it proves; see the README there."""
    return json.loads((TED_FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def ted_fixture_names() -> list[str]:
    return sorted(path.stem for path in TED_FIXTURES_DIR.glob("*.json"))


@pytest.fixture(params=ted_fixture_names())
def ted_fixture_name(request: pytest.FixtureRequest) -> str:
    """Every recorded notice in turn, so a new fixture is exercised automatically."""
    return str(request.param)


@pytest.fixture(scope="session")
def rules_config() -> RulesConfig:
    """The seeded rule set as shipped. Tests read the real vocabulary, not a mock."""
    return load_rules_config(CONFIG_DIR / "rules.yaml")


@pytest.fixture(scope="session")
def rule_engine(rules_config: RulesConfig) -> RuleEngine:
    return RuleEngine(rules_config)


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


@pytest.fixture
def run_id() -> str:
    """The run every stored tender in a test belongs to.

    ``upsert_tenders`` requires one, so this is shared rather than written out at
    each call site: a literal repeated forty times is a habit, and the point of
    the argument is that traceability stops being a habit.
    """
    return "run-test-0001"


TenderFactory = Callable[..., Tender]


@pytest.fixture
def make_tender() -> TenderFactory:
    """Build a plausible tender. Anything else a test needs, set with ``model_copy``."""
    return _make_tender


def _make_tender(
    source_id: str = "00123456-2026",
    *,
    source: SourcePlatform = SourcePlatform.TED,
    title: str = "Norway - Feasibility study, advisory service, analysis - Hydrogen pre-FEED",
    title_native: str | None = "Feasibility study for a hydrogen production facility",
    description: str | None = "Pre-FEED study including techno-economic analysis.",
    buyer_country: str | None = "NOR",
    performance_countries: list[str] | None = None,
    published_date: date | None = date(2026, 3, 1),
    deadline: datetime | None = datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
    deadline_date: date | None = date(2026, 4, 15),
    deadline_type: DeadlineType = DeadlineType.TENDER_SUBMISSION,
    notice_stage: NoticeStage = NoticeStage.CONTRACT_NOTICE,
    source_version: str | None = "1",
    cpv_main: str | None = "71241000",
    cpv_all: list[str] | None = None,
    contract_natures: list[ContractNature] | None = None,
    seen_at: datetime = datetime(2026, 3, 2, 6, 0, tzinfo=UTC),
) -> Tender:
    blocks = []
    if title_native:
        blocks.append(TextBlock(field="title-proc", language="eng", text=title_native))
    if description:
        blocks.append(TextBlock(field="description-proc", language="eng", text=description))

    return Tender(
        source=source,
        source_id=source_id,
        source_version=source_version,
        source_url=f"https://ted.europa.eu/notice/{source_id}",
        title=title,
        title_language="eng",
        title_native=title_native,
        title_native_language="eng" if title_native else None,
        description=description,
        buyer_name="Statsbygg",
        buyer_country=buyer_country,
        place_of_performance="NO081, NOR",
        place_of_performance_country=(
            performance_countries if performance_countries is not None else ["NOR"]
        ),
        published_date=published_date,
        deadline=deadline,
        deadline_date=deadline_date,
        deadline_source="deadline-receipt-tender-date-lot+deadline-receipt-tender-time-lot",
        deadline_type=deadline_type,
        notice_stage=notice_stage,
        notice_subtype="16",
        contract_nature=ContractNature.SERVICES,
        contract_natures=(
            contract_natures if contract_natures is not None else [ContractNature.SERVICES]
        ),
        cpv_main=cpv_main,
        cpv_additional=["73210000"],
        cpv_all=cpv_all if cpv_all is not None else ["71241000", "73210000"],
        estimated_value=Decimal("250000.00"),
        currency="NOK",
        documents_url=f"https://ted.europa.eu/notice/{source_id}/documents",
        languages=["ENG", "NOR"],
        multi_lot=False,
        screening_blocks=blocks,
        first_seen_at=seen_at,
        last_seen_at=seen_at,
        raw={"publication-number": source_id},
    )
