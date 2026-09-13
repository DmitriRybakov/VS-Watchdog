"""The register in the browser: escaping, safe links, states and the keyboard loop.

Every test here is about something a colleague would see. The escaping ones are
about something they would not see until it was too late.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import UTC, date, datetime

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

RUN = "run-page-test"

# What an attacker would put in a notice title if they could. TED is a public
# system that accepts text from thousands of buyers, so "they could" is the
# assumption this file is written under.
SCRIPT_TITLE = '<script>alert("xss")</script> Hydrogen study'
JAVASCRIPT_URL = "javascript:alert(1)"


def _tender(
    source_id: str = "1-2026",
    *,
    title: str = "Denmark - Engineering services - Brintanlaeg",
    native: str = "Forundersogelse af brintanlaeg",
    url: str | None = "https://ted.europa.eu/notice/1-2026",
    deadline: date | None = date(2026, 9, 30),
) -> Tender:
    return Tender(
        source=SourcePlatform.TED,
        source_id=source_id,
        source_url=url,
        title=title,
        title_language="eng",
        title_native=native,
        title_native_language="dan",
        description="A feasibility study.",
        buyer_name="Energinet",
        buyer_country="DNK",
        place_of_performance_country=["DNK"],
        published_date=date(2026, 9, 1),
        deadline_date=deadline,
        deadline_type=DeadlineType.PARTICIPATION_REQUEST,
        notice_stage=NoticeStage.CONTRACT_NOTICE,
        contract_nature=ContractNature.SERVICES,
        contract_natures=[ContractNature.SERVICES],
        screening_blocks=[TextBlock(field="title-proc", language="dan", text=native)],
    )


def _result(tender: Tender, *, evidence: str = "Forundersogelse af brintanlaeg") -> ScreeningResult:
    return ScreeningResult(
        tender_id=tender.id,
        score=3,
        band=Band.REVIEW,
        rules_only_score=3,
        confidence=0.6,
        reason_codes=["RULES_ONLY"],
        domain_strength_rank=3,
        domain_rules_matched=1,
        rules=RulesResult(
            tender_id=tender.id,
            matches=[
                RuleMatch(
                    rule_id="hydrogen",
                    signal=RuleSignal.DOMAIN,
                    strength=RuleStrength.HIGH,
                    alias_matched="brint",
                    field="title-proc",
                    evidence=evidence,
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


@pytest.fixture
def loaded(repository: Repository) -> Repository:
    tender = _tender()
    repository.upsert_tenders([tender], run_id=RUN)
    repository.save_screening_result(_result(tender))
    return repository


# -------------------------------------------------------------------- escaping


def test_a_script_tag_in_a_title_is_escaped_everywhere_it_appears(
    client: TestClient, repository: Repository
) -> None:
    tender = _tender(title=SCRIPT_TITLE, native=SCRIPT_TITLE)
    repository.upsert_tenders([tender], run_id=RUN)
    repository.save_screening_result(_result(tender, evidence=SCRIPT_TITLE))

    for url in ("/register?past=1", "/register?past=1&mode=table", f"/register/notice/{tender.id}"):
        html = client.get(url).text
        assert "<script>alert(" not in html, url
        assert "&lt;script&gt;" in html, url


def test_a_javascript_url_is_not_rendered_as_a_link(
    client: TestClient, repository: Repository
) -> None:
    tender = _tender(url=JAVASCRIPT_URL)
    repository.upsert_tenders([tender], run_id=RUN)
    repository.save_screening_result(_result(tender))

    html = client.get("/register?past=1").text

    assert "javascript:" not in html


def test_an_http_url_survives(client: TestClient, loaded: Repository) -> None:
    html = client.get("/register?past=1").text

    assert "https://ted.europa.eu/notice/1-2026" in html


# ---------------------------------------------------------------------- states


def test_an_empty_database_says_what_to_press(client: TestClient) -> None:
    html = client.get("/register").text

    assert "No notices yet" in html
    assert "Update from TED" in html


def test_no_results_for_these_filters_is_a_different_sentence(
    client: TestClient, loaded: Repository
) -> None:
    html = client.get("/register?q=nothing-matches-this&band=all&past=1").text

    assert "No notices match these filters" in html
    assert "Nothing has been deleted" in html


def test_hiding_past_deadlines_offers_to_bring_them_back(
    client: TestClient, repository: Repository
) -> None:
    tender = _tender(deadline=date(2026, 1, 1))
    repository.upsert_tenders([tender], run_id=RUN)
    repository.save_screening_result(_result(tender))

    html = client.get("/register").text

    assert "No open notices match these filters" in html
    assert "Include past-deadline notices" in html


def test_the_provider_being_off_is_stated_in_plain_words(
    client: TestClient, loaded: Repository
) -> None:
    html = client.get("/register?past=1").text

    assert "AI assessment is off" in html
    assert "keyword and CPV matches ordered by evidence strength, not judged relevance" in html


def test_a_rules_grade_is_not_shown_as_a_score(client: TestClient, loaded: Repository) -> None:
    """The chip says E3, not 3, and the caption says what kind of claim it is."""
    html = client.get("/register?past=1").text

    assert "chip-evidence" in html
    assert "E3" in html
    assert "not a judged score" in html


def test_unscreened_notices_are_announced(client: TestClient, repository: Repository) -> None:
    repository.upsert_tenders([_tender("55-2026")], run_id=RUN)

    html = client.get("/register").text

    assert "never been screened" in html


def test_a_bad_filter_is_a_sentence_and_not_a_stack_trace(
    client: TestClient, loaded: Repository
) -> None:
    response = client.get("/register?band=urgent")

    assert response.status_code == 400
    assert "not a valid band" in response.text
    assert "Traceback" not in response.text


def test_an_unknown_notice_is_a_sentence_and_not_a_stack_trace(client: TestClient) -> None:
    response = client.get("/register/notice/ted:nope")

    assert response.status_code == 404
    assert "holds no notice" in response.text
    assert "Traceback" not in response.text


# ----------------------------------------------------------------- interaction


def test_htmx_asks_for_a_fragment_and_gets_one(client: TestClient, loaded: Repository) -> None:
    response = client.get("/register?past=1", headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert "<html" not in response.text
    assert "toolbar" in response.text


def test_recording_a_verdict_returns_the_list_rather_than_navigating(
    client: TestClient, loaded: Repository
) -> None:
    client.post("/register/reviewer", data={"name": "Ada Lovelace"})

    response = client.post(
        "/register/ted:1-2026/verdict?past=1&cursor=0",
        data={"verdict": "relevant"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "<html" not in response.text
    assert "Relevant" in response.text


def test_a_verdict_is_stored_as_human_judgement(client: TestClient, loaded: Repository) -> None:
    client.post("/register/reviewer", data={"name": "Ada Lovelace"})

    client.post(
        "/register/ted:1-2026/verdict?past=1",
        data={"verdict": "not_relevant"},
        headers={"HX-Request": "true"},
    )

    review = loaded.get_review("ted:1-2026")
    assert review is not None
    assert review.verdict.value == "not_relevant"
    assert review.reviewed_by == "Ada Lovelace"


def test_an_invalid_verdict_is_refused(client: TestClient, loaded: Repository) -> None:
    response = client.post(
        "/register/ted:1-2026/verdict", data={"verdict": "probably"}, headers={"HX-Request": "true"}
    )

    assert response.status_code == 400
    assert "not a verdict" in response.text


def test_the_keyboard_layer_is_served_from_our_own_static_folder(
    client: TestClient, loaded: Repository
) -> None:
    html = client.get("/register?past=1").text
    script = client.get("/static/js/triage.js").text

    assert "/static/js/triage.js" in html
    for key in ('case "j"', 'case "k"', 'case "y"', 'case "n"', 'case "u"', 'case "/"'):
        assert key in script


def test_the_deadline_type_is_shown_beside_the_date(client: TestClient, loaded: Repository) -> None:
    """A participation request is not a bid deadline and must not read as one."""
    html = client.get("/register?past=1").text

    assert "Request to participate" in html
    assert "Bid due" not in html


def test_the_composed_title_is_the_line_and_the_native_title_is_labelled(
    client: TestClient, loaded: Repository
) -> None:
    html = client.get("/register?past=1").text

    assert "Denmark - Engineering services - Brintanlaeg" in html
    assert "Buyer&#39;s own title" in html or "Buyer's own title" in html
    assert "Forundersogelse af brintanlaeg" in html


def test_the_detail_page_never_calls_the_composed_line_a_translation(
    client: TestClient, loaded: Repository
) -> None:
    html = client.get("/register/notice/ted:1-2026").text

    assert "not translated" in html


# --------------------------------------------------------------------- exports


def test_the_csv_export_is_the_current_filter(client: TestClient, loaded: Repository) -> None:
    filtered = client.get("/register/export.csv?band=all&past=1&q=nothing-matches")
    everything = client.get("/register/export.csv?band=all&past=1")

    assert len(list(csv.reader(io.StringIO(filtered.text)))) == 1, "header only"
    assert len(list(csv.reader(io.StringIO(everything.text)))) == 2


def test_the_csv_export_prefixes_a_formula(client: TestClient, repository: Repository) -> None:
    tender = _tender(title="=1+1 Hydrogen")
    repository.upsert_tenders([tender], run_id=RUN)
    repository.save_screening_result(_result(tender))

    body = client.get("/register/export.csv?band=all&past=1").text

    assert "'=1+1 Hydrogen" in body


def test_the_export_is_streamed_as_an_attachment(client: TestClient, loaded: Repository) -> None:
    csv_response = client.get("/register/export.csv?band=all&past=1")
    xlsx_response = client.get("/register/export.xlsx?band=all&past=1")

    assert "attachment" in csv_response.headers["content-disposition"]
    assert "attachment" in xlsx_response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(xlsx_response.content)) as archive:
        assert archive.testzip() is None


# ------------------------------------------------------------------ provenance


def test_a_machine_result_and_a_human_review_are_labelled_apart(
    client: TestClient, loaded: Repository
) -> None:
    client.post("/register/reviewer", data={"name": "Ada Lovelace"})
    client.post(
        "/register/ted:1-2026/verdict?past=1",
        data={"verdict": "unsure"},
        headers={"HX-Request": "true"},
    )

    html = client.get("/register/notice/ted:1-2026").text

    assert "Watchdog&#39;s own screening" in html or "Watchdog's own screening" in html
    assert "Source facts" in html
    assert "Decisions" in html


def test_every_time_on_screen_says_it_is_utc(client: TestClient, loaded: Repository) -> None:
    html = client.get("/register/notice/ted:1-2026").text

    assert "UTC" in html
    assert datetime.now(UTC).strftime("%Y-%m-%d") in html or "first seen" in html.lower()
