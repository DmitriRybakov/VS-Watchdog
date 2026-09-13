"""Viewing a page must never call TED or a language model.

This is enforced at the lowest level available: the socket. Patching httpx would
only prove that httpx was not used, and the failure worth preventing is any
outbound call at all - a page that quietly fetched a notice would be slow,
expensive and, on a bad day, would fail because somebody else's server was down.
"""

from __future__ import annotations

import socket
from datetime import date

import pytest
from fastapi.testclient import TestClient

from watchdog.core.enums import (
    Band,
    ContractNature,
    DeadlineType,
    Domain,
    NoticeStage,
    RuleSignal,
    RulesRoute,
    RuleStrength,
    SourcePlatform,
)
from watchdog.core.models import (
    RuleMatch,
    RulesResult,
    ScreeningResult,
    Tender,
    TextBlock,
)
from watchdog.storage.repository import Repository


class OutboundCall(AssertionError):
    """Raised the moment anything tries to open a connection."""


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise OutboundCall(f"a page tried to open a connection: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def loaded(repository: Repository) -> Repository:
    tender = Tender(
        source=SourcePlatform.TED,
        source_id="77-2026",
        source_url="https://ted.europa.eu/notice/77-2026",
        title="Denmark - Engineering services - Brintanlaeg",
        title_native="Forundersogelse af brintanlaeg",
        title_native_language="dan",
        buyer_name="Energinet",
        buyer_country="DNK",
        place_of_performance_country=["DNK"],
        published_date=date(2026, 9, 1),
        deadline_date=date(2026, 9, 30),
        deadline_type=DeadlineType.TENDER_SUBMISSION,
        notice_stage=NoticeStage.CONTRACT_NOTICE,
        contract_nature=ContractNature.SERVICES,
        contract_natures=[ContractNature.SERVICES],
        screening_blocks=[
            TextBlock(field="title-proc", language="dan", text="Forundersogelse af brintanlaeg")
        ],
    )
    repository.upsert_tenders([tender], run_id="run-no-network")
    repository.save_screening_result(
        ScreeningResult(
            tender_id=tender.id,
            score=3,
            band=Band.REVIEW,
            rules_only_score=3,
            confidence=0.6,
            rules=RulesResult(
                tender_id=tender.id,
                matches=[
                    RuleMatch(
                        rule_id="hydrogen",
                        signal=RuleSignal.DOMAIN,
                        strength=RuleStrength.HIGH,
                        alias_matched="brint",
                        field="title-proc",
                        evidence="Forundersogelse af brintanlaeg",
                    )
                ],
                domains_hit=[Domain.HYDROGEN],
                route=RulesRoute.ASSESS,
                rules_version="3",
            ),
            explanation="Rules-only grade 3.",
            screened_content_hash=tender.content_hash,
            provider="disabled",
            rules_version="3",
            policy_version="1",
            profile_version="1",
        )
    )
    return repository


@pytest.mark.parametrize(
    "url",
    [
        "/register",
        "/register?past=1",
        "/register?past=1&mode=table",
        "/register?band=all&past=1&domain=hydrogen",
        "/register/results?past=1",
        "/register/notice/ted:77-2026",
        "/register/export.csv?band=all&past=1",
        "/register/export.xlsx?band=all&past=1",
        "/runs",
        "/runs/status",
        "/health",
    ],
)
def test_rendering_a_page_opens_no_connection(
    client: TestClient, loaded: Repository, no_network: None, url: str
) -> None:
    response = client.get(url)

    assert response.status_code == 200


def test_recording_a_verdict_opens_no_connection(
    client: TestClient, loaded: Repository, no_network: None
) -> None:
    client.post("/register/reviewer", data={"name": "Ada Lovelace"})

    response = client.post(
        "/register/ted:77-2026/verdict?past=1",
        data={"verdict": "relevant"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200


def test_the_guard_itself_works(no_network: None) -> None:
    """If this ever stops raising, every test above is proving nothing."""
    with pytest.raises(OutboundCall):
        socket.create_connection(("example.invalid", 80))
