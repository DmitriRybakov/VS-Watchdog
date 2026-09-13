"""The register in the browser: escaping, safe links, states and the keyboard loop.

Every test here is about something a colleague would see. The escaping ones are
about something they would not see until it was too late.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from tests.conftest import load_ted_fixture

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
from watchdog.sources.ted import map_notice
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


def _failed_assessment(tender: Tender) -> ScreeningResult:
    """What `_assessment_failed` stores: a model was asked and did not answer.

    No assessment and no rules grade - the rules were never going to produce the
    score, a model was. Both of the obvious branches are therefore false, which
    is how "ENone" got onto the screen.
    """
    return ScreeningResult(
        tender_id=tender.id,
        score=3,
        band=Band.REVIEW,
        rules_only_score=None,
        confidence=0.4,
        reason_codes=["ASSESSMENT_FAILED"],
        rules=RulesResult(tender_id=tender.id, route=RulesRoute.ASSESS, rules_version="3"),
        assessment=None,
        explanation="The assessment did not come back.",
        ai_enabled=True,
        screened_content_hash=tender.content_hash,
        provider="azure_openai",
        model="gpt-4o",
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


# ------------------------------------------------------- the detail page proper


def _badge(html: str) -> str:
    """The first deadline badge on a page, as a colleague reads it."""
    found = re.search(r'<span class="badge badge-\w+">([^<]+)</span>', html)
    assert found is not None, "no deadline badge on the page"
    return found.group(1).strip()


def test_the_deadline_chip_is_the_same_on_the_register_and_on_the_detail_page(
    client: TestClient, loaded: Repository
) -> None:
    """The detail page used to say "No deadline given" beside a stated deadline."""
    register = client.get("/register?past=1").text
    detail = client.get("/register/notice/ted:1-2026").text

    assert "No deadline given" not in detail
    assert _badge(register) == _badge(detail)


def test_a_notice_with_no_deadline_says_so_in_its_own_words(
    client: TestClient, repository: Repository
) -> None:
    tender = _tender(source_id="2-2026", deadline=None)
    repository.upsert_tenders([tender], run_id=RUN)
    repository.save_screening_result(_result(tender))

    html = client.get(f"/register/notice/{tender.id}").text

    assert "No deadline of any kind" in html


def test_the_deadline_time_is_shown_with_its_zone_when_there_is_one(
    client: TestClient, repository: Repository
) -> None:
    tender = _tender(source_id="3-2026").model_copy(
        update={"deadline": datetime(2026, 9, 30, 12, 0, tzinfo=UTC)}
    )
    repository.upsert_tenders([tender], run_id=RUN)
    repository.save_screening_result(_result(tender))

    html = client.get(f"/register/notice/{tender.id}").text

    assert "2026-09-30 12:00 UTC" in html


def test_the_description_is_shown_whole_with_an_expander_for_the_rest(
    client: TestClient, repository: Repository
) -> None:
    paragraphs = ["First paragraph. " + "a" * 700, "Second. " + "b" * 700, "Third. " + "c" * 700]
    tender = _tender(source_id="4-2026").model_copy(update={"description": "\n\n".join(paragraphs)})
    repository.upsert_tenders([tender], run_id=RUN)
    repository.save_screening_result(_result(tender))

    html = client.get(f"/register/notice/{tender.id}").text

    assert "Show the rest of the description" in html
    assert "2 more paragraphs" in html
    for paragraph in paragraphs:
        assert paragraph in html, "a description is folded, never cut"


def test_work_outside_the_buyers_country_is_stated_and_countries_are_named(
    client: TestClient, repository: Repository
) -> None:
    """619675-2026: a French buyer procuring a waste roadmap for Angola."""
    tender = _tender(source_id="619675-2026").model_copy(
        update={"buyer_country": "FRA", "place_of_performance_country": ["AGO"]}
    )
    repository.upsert_tenders([tender], run_id=RUN)
    repository.save_screening_result(_result(tender))

    html = client.get(f"/register/notice/{tender.id}").text

    assert "Work outside the buyer's country" in html
    assert "France" in html
    assert "Angola" in html
    assert ">FRA<" not in html


def test_the_three_empty_states_read_differently(
    client: TestClient, repository: Repository
) -> None:
    tender = _tender(source_id="5-2026").model_copy(
        update={"description": None, "buyer_name": None}
    )
    repository.upsert_tenders([tender], run_id=RUN)
    repository.save_screening_result(_result(tender))

    html = client.get(f"/register/notice/{tender.id}").text

    assert "Not provided in the retrieved data" in html, "the platform sent no value"
    assert "Not retrieved yet" in html, "we have not read the documents"
    assert "AI assessment has not run" in html, "no judgement was made"


def test_no_confidence_percentage_is_shown_when_nothing_judged_the_notice(
    client: TestClient, loaded: Repository
) -> None:
    """A percentage beside three unestablished axes reads as a chance of relevance."""
    html = client.get("/register/notice/ted:1-2026").text

    assert "Keyword evidence: strong, 3 of 3" in html
    assert "AI assessment has not run" in html
    assert "60%" not in html


# ------------------------------------------------------- the failed assessment


@pytest.fixture
def failed(repository: Repository) -> Tender:
    tender = _tender(source_id="9-2026")
    repository.upsert_tenders([tender], run_id=RUN)
    repository.save_screening_result(_failed_assessment(tender))
    return tender


@pytest.mark.parametrize(
    "url",
    [
        "/register?past=1&band=all",
        "/register?past=1&band=all&mode=table",
        "/register?past=1&band=all&mode=triage",
        "/register/notice/ted:9-2026",
    ],
    ids=["card", "table", "triage", "detail"],
)
def test_a_failed_assessment_never_renders_as_a_grade(
    client: TestClient, failed: Tender, url: str
) -> None:
    """The bug this replaces printed the chip as "ENone" on three screens at once.

    A model ran and did not answer, so there is no assessment and no rules grade.
    Read as a grade it looks like the lowest mark there is, which is a claim about
    the notice rather than about our own failure.
    """
    html = client.get(url).text

    assert "ENone" not in html
    assert ">None<" not in html
    assert "Assessment failed" in html or "did not come back" in html


def test_the_failed_assessment_chip_is_neither_a_score_nor_a_grade(
    client: TestClient, failed: Tender
) -> None:
    html = client.get("/register?past=1&band=all").text

    assert "chip-failed" in html
    assert "chip-evidence" not in html, "it must not wear the rules-grade shape"
    assert "chip-score" not in html, "and it must not wear the assessed-score shape"


def test_a_rules_grade_still_reads_as_a_grade(client: TestClient, loaded: Repository) -> None:
    # The guard above must not have been bought by breaking the ordinary case.
    html = client.get("/register?past=1&band=all").text

    assert "chip-evidence" in html
    assert "E3" in html
    assert "chip-failed" not in html


def test_the_detail_page_explains_a_failed_assessment_and_says_it_is_retried(
    client: TestClient, failed: Tender
) -> None:
    html = client.get("/register/notice/ted:9-2026").text

    assert "did not come back" in html
    assert "retried on the next screening run" in html
    assert "%" not in html.split("Reason codes")[0].split("Band")[-1]


# ------------------------------------------- the wider projection, end to end


@pytest.fixture
def multi_lot(repository: Repository) -> Tender:
    """A real recorded notice, mapped and stored, not a hand-built one.

    This is the only test that takes the new fields through the database. The
    list columns hold Decimals and dates, which JSON cannot, so a round trip is
    the thing that would break rather than the mapping.
    """
    tender = map_notice(load_ted_fixture("lot_values_and_languages_do_not_match_the_lots"))
    repository.upsert_tenders([tender], run_id=RUN)
    repository.save_screening_result(_result(tender))
    return tender


def test_the_new_fields_survive_the_database(repository: Repository, multi_lot: Tender) -> None:
    stored = repository.get_tender(multi_lot.id)

    assert stored is not None
    assert stored.lot_ids == multi_lot.lot_ids
    assert stored.lot_values == multi_lot.lot_values, "Decimals through a JSON column"
    assert stored.contract_start_dates == multi_lot.contract_start_dates, "dates likewise"
    assert stored.submission_languages == ["CAT", "SPA"]
    assert stored.award_criteria == multi_lot.award_criteria
    assert stored.estimated_value == multi_lot.estimated_value


def test_the_detail_page_says_a_lot_value_is_across_lots_not_for_one(
    client: TestClient, multi_lot: Tender
) -> None:
    html = client.get(f"/register/notice/{multi_lot.id}").text

    assert "5 lots" in html
    assert "across lots" in html
    assert "Catalan, Spanish" in html
    # The claim that must never appear: a language against a particular lot.
    assert "LOT-0001: Catalan" not in html
    assert "not matched to particular lots" in html


def test_the_detail_page_names_the_procedure_and_the_sector(
    client: TestClient, multi_lot: Tender
) -> None:
    html = client.get(f"/register/notice/{multi_lot.id}").text

    assert "Open - anyone may tender" in html
    assert "there is no separate qualification stage" in html


def test_the_register_export_labels_an_across_lots_column_as_one(
    client: TestClient, multi_lot: Tender
) -> None:
    rows = list(csv.reader(io.StringIO(client.get("/register/export.csv?band=all&past=1").text)))
    header, values = rows[0], rows[1]
    cells = dict(zip(header, values, strict=True))

    assert "submission_languages_across_lots" in header
    assert cells["submission_languages_across_lots"] == "CAT SPA"
    assert cells["lot_count"] == "5"


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
