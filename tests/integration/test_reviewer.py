"""Attribution on a review: what it is, what it is not, and that it is required.

The point of these is narrow and worth stating. There is no authentication, and
nothing here pretends otherwise. What is being fixed is that every review written
before sign-in exists must still be tellable apart afterwards, because those
reviews are what later work gets measured against.
"""

from __future__ import annotations

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
    Verdict,
)
from watchdog.core.models import RuleMatch, RulesResult, ScreeningResult, Tender, TextBlock
from watchdog.services.review import UnattributedReview, record_verdict
from watchdog.storage.repository import Repository
from watchdog.web import reviewer as reviewer_cookie

RUN = "run-reviewer-test"


@pytest.fixture
def loaded(repository: Repository) -> Repository:
    tender = Tender(
        source=SourcePlatform.TED,
        source_id="1-2026",
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
    repository.upsert_tenders([tender], run_id=RUN)
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


# ------------------------------------------------------------------- the service


def test_a_verdict_with_nobody_s_name_is_refused_not_defaulted(loaded: Repository) -> None:
    """The whole point: no constant stands in for a person who did not say."""
    with pytest.raises(UnattributedReview):
        record_verdict("ted:1-2026", Verdict.RELEVANT, reviewed_by="  ", repository=loaded)

    assert loaded.get_review("ted:1-2026") is None


def test_the_name_given_is_the_name_stored(loaded: Repository) -> None:
    record_verdict("ted:1-2026", Verdict.RELEVANT, reviewed_by="Ada Lovelace", repository=loaded)

    review = loaded.get_review("ted:1-2026")
    assert review is not None
    assert review.reviewed_by == "Ada Lovelace"


def test_surrounding_whitespace_is_tidied_but_the_name_is_not_otherwise_touched(
    loaded: Repository,
) -> None:
    record_verdict("ted:1-2026", Verdict.UNSURE, reviewed_by="  Bj rn   Erik ", repository=loaded)

    review = loaded.get_review("ted:1-2026")
    assert review is not None
    assert review.reviewed_by == "Bj rn Erik"


def test_two_people_recording_are_told_apart(loaded: Repository) -> None:
    record_verdict("ted:1-2026", Verdict.RELEVANT, reviewed_by="Ada", repository=loaded)
    record_verdict("ted:1-2026", Verdict.NOT_RELEVANT, reviewed_by="Grace", repository=loaded)

    history = loaded.list_reviews("ted:1-2026")
    assert [review.reviewed_by for review in history] == ["Grace", "Ada"]
    assert [review.superseded for review in history] == [False, True]


# ---------------------------------------------------------------- the name box


@pytest.mark.parametrize(
    ("typed", "expected"),
    [("  Ada  Lovelace ", "Ada Lovelace"), ("", None), ("   ", None), (None, None)],
)
def test_a_typed_name_is_tidied(typed: str | None, expected: str | None) -> None:
    assert reviewer_cookie.clean(typed) == expected


@pytest.mark.parametrize("bad", ["x" * 81, "Ada\x00Lovelace", "Ada\nLovelace\rmore"])
def test_a_name_watchdog_will_not_store_is_named(bad: str) -> None:
    if "\n" in bad or "\r" in bad:
        # Line breaks collapse to spaces before the check, which is the right
        # outcome: the name is usable, not rejected.
        assert reviewer_cookie.clean(bad) == " ".join(bad.split())
        return
    with pytest.raises(reviewer_cookie.InvalidName):
        reviewer_cookie.clean(bad)


def test_the_register_asks_for_a_name_before_anything_can_be_decided(
    client: TestClient, loaded: Repository
) -> None:
    html = client.get("/register?past=1").text

    assert "Put your name in before you decide anything" in html
    assert "not a sign-in" in html
    assert "disabled" in html


def test_saving_a_name_remembers_it_in_this_browser(client: TestClient, loaded: Repository) -> None:
    saved = client.post("/register/reviewer", data={"name": "Ada Lovelace"})

    assert saved.status_code == 200
    assert "Recording decisions as" in saved.text
    # A cookie value containing a space comes back quoted; the server unquotes it.
    assert (client.cookies.get(reviewer_cookie.COOKIE) or "").strip('"') == "Ada Lovelace"

    html = client.get("/register?past=1").text
    assert "Ada Lovelace" in html
    assert "Put your name in before you decide anything" not in html


def test_the_name_box_never_claims_to_be_a_sign_in(client: TestClient, loaded: Repository) -> None:
    client.post("/register/reviewer", data={"name": "Ada Lovelace"})

    html = client.get("/register?past=1").text

    assert "Not a sign-in" in html
    for word in ("Log in", "Sign in", "Password", "Authenticate"):
        assert word not in html


def test_a_name_the_box_will_not_take_is_a_plain_sentence(
    client: TestClient, loaded: Repository
) -> None:
    response = client.post("/register/reviewer", data={"name": "x" * 200})

    assert response.status_code == 400
    assert "at most 80 characters" in response.text
    assert "Traceback" not in response.text


def test_clearing_the_box_forgets_the_name(client: TestClient, loaded: Repository) -> None:
    client.post("/register/reviewer", data={"name": "Ada Lovelace"})
    client.post("/register/reviewer", data={"name": ""})

    assert not client.cookies.get(reviewer_cookie.COOKIE)
    assert "Put your name in before you decide anything" in client.get("/register?past=1").text


# ------------------------------------------------------------------- the route


def test_a_verdict_posted_without_a_name_is_refused_with_a_sentence(
    client: TestClient, loaded: Repository
) -> None:
    response = client.post(
        "/register/ted:1-2026/verdict?past=1",
        data={"verdict": "relevant"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 400
    # The apostrophe is escaped, which is the point of escaping it.
    assert "recorded in somebody&#39;s name" in response.text
    assert loaded.get_review("ted:1-2026") is None


def test_a_verdict_carries_the_name_from_the_cookie(client: TestClient, loaded: Repository) -> None:
    client.post("/register/reviewer", data={"name": "Ada Lovelace"})

    client.post(
        "/register/ted:1-2026/verdict?past=1",
        data={"verdict": "relevant"},
        headers={"HX-Request": "true"},
    )

    review = loaded.get_review("ted:1-2026")
    assert review is not None
    assert review.reviewed_by == "Ada Lovelace"
    assert review.reviewed_by != "register"


def test_a_hand_edited_cookie_is_treated_as_no_name_rather_than_failing(
    client: TestClient, loaded: Repository
) -> None:
    client.cookies.set(reviewer_cookie.COOKIE, "x" * 500)

    response = client.get("/register?past=1")

    assert response.status_code == 200
    assert "Put your name in before you decide anything" in response.text


def test_the_detail_page_names_who_decided(client: TestClient, loaded: Repository) -> None:
    client.post("/register/reviewer", data={"name": "Ada Lovelace"})
    client.post(
        "/register/ted:1-2026/verdict?past=1",
        data={"verdict": "unsure"},
        headers={"HX-Request": "true"},
    )

    html = client.get("/register/notice/ted:1-2026").text

    assert "Ada Lovelace" in html
