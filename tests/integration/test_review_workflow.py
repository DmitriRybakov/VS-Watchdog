"""The full review: what a colleague decided, who decided it, and what came before.

The whole point of the review being append-only is on this page. A colleague who
changes their mind keeps the earlier verdict, its note, its author and its
timestamp, and all of it stays visible - because why somebody changed their mind
is worth more than the verdict they landed on.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from watchdog.core.enums import Band, BidRoute, RulesRoute, Verdict
from watchdog.core.models import RulesResult, ScreeningResult, Tender
from watchdog.storage.repository import Repository

RUN = "run-review-0001"


def _screen(repository: Repository, tender: Tender) -> None:
    repository.save_screening_result(
        ScreeningResult(
            tender_id=tender.id,
            score=3,
            band=Band.REVIEW,
            rules_only_score=2,
            confidence=0.5,
            rules=RulesResult(
                tender_id=tender.id,
                matches=[],
                domains_hit=[],
                route=RulesRoute.ASSESS,
                rules_version="3",
            ),
            explanation="Keyword evidence only.",
            screened_content_hash=tender.content_hash,
            provider="disabled",
            rules_version="3",
            policy_version="1",
            profile_version="1",
        )
    )


def _loaded(repository: Repository, make_tender, count: int = 3) -> list[Tender]:
    tenders = [make_tender(source_id=f"{index}-2026") for index in range(1, count + 1)]
    repository.upsert_tenders(tenders, run_id=RUN)
    for tender in tenders:
        _screen(repository, tender)
    return tenders


def _as(client: TestClient, name: str = "Ada Lovelace") -> None:
    client.post("/register/reviewer", data={"name": name})


def test_the_whole_review_is_recorded_not_only_the_verdict(
    client: TestClient, repository: Repository, make_tender
) -> None:
    tender = _loaded(repository, make_tender, count=1)[0]
    _as(client)

    response = client.post(
        f"/register/{tender.id}/review",
        data={
            "verdict": "relevant",
            "bid_route": "partner_route",
            "owner": "Grace Hopper",
            "next_action": "Call the client before Friday",
            "note": "Pre-FEED, our kind of work.",
        },
    )

    assert response.status_code == 200
    review = repository.get_review(tender.id)
    assert review is not None
    assert review.verdict is Verdict.RELEVANT
    assert review.bid_route is BidRoute.PARTNER_ROUTE
    assert review.owner == "Grace Hopper"
    assert review.next_action == "Call the client before Friday"
    assert review.note == "Pre-FEED, our kind of work."
    assert review.reviewed_by == "Ada Lovelace"


def test_changing_your_mind_keeps_the_earlier_decision_whole(
    client: TestClient, repository: Repository, make_tender
) -> None:
    tender = _loaded(repository, make_tender, count=1)[0]
    _as(client, "Ada Lovelace")
    client.post(
        f"/register/{tender.id}/review",
        data={"verdict": "relevant", "bid_route": "direct_bid", "note": "Looks like ours."},
    )

    _as(client, "Grace Hopper")
    client.post(
        f"/register/{tender.id}/review",
        data={
            "verdict": "not_relevant",
            "bid_route": "no_bid",
            "note": "Construction, not advisory.",
        },
    )

    history = repository.list_reviews(tender.id)

    assert len(history) == 2
    assert history[0].verdict is Verdict.NOT_RELEVANT
    assert history[0].superseded is False
    assert history[1].verdict is Verdict.RELEVANT
    assert history[1].bid_route is BidRoute.DIRECT_BID
    assert history[1].note == "Looks like ours."
    assert history[1].reviewed_by == "Ada Lovelace"
    assert history[1].superseded is True


def test_the_history_and_who_decided_are_on_the_page(
    client: TestClient, repository: Repository, make_tender
) -> None:
    tender = _loaded(repository, make_tender, count=1)[0]
    _as(client)
    client.post(
        f"/register/{tender.id}/review",
        data={"verdict": "unsure", "bid_route": "watch", "note": "Waiting on the documents."},
    )

    html = client.get(f"/register/notice/{tender.id}").text

    assert "Every decision recorded on this notice" in html
    assert "Ada Lovelace" in html
    assert "Waiting on the documents." in html
    assert "Watch" in html
    assert "UTC" in html


def test_a_quick_verdict_from_the_queue_does_not_wipe_a_recorded_bid_route(
    client: TestClient, repository: Repository, make_tender
) -> None:
    """``y`` says something about the verdict and nothing about the bid route."""
    tender = _loaded(repository, make_tender, count=1)[0]
    _as(client)
    client.post(
        f"/register/{tender.id}/review",
        data={"verdict": "relevant", "bid_route": "partner_route", "owner": "Grace Hopper"},
    )

    client.post(
        f"/register/{tender.id}/verdict?past=1",
        data={"verdict": "unsure"},
        headers={"HX-Request": "true"},
    )

    review = repository.get_review(tender.id)
    assert review is not None
    assert review.verdict is Verdict.UNSURE
    assert review.bid_route is BidRoute.PARTNER_ROUTE
    assert review.owner == "Grace Hopper"


def test_a_review_with_nobodys_name_on_it_is_refused(
    client: TestClient, repository: Repository, make_tender
) -> None:
    tender = _loaded(repository, make_tender, count=1)[0]

    response = client.post(f"/register/{tender.id}/review", data={"verdict": "relevant"})

    assert response.status_code == 400
    assert "somebody&#39;s name" in response.text or "somebody's name" in response.text
    assert repository.get_review(tender.id) is None


def test_a_bid_route_that_is_not_one_is_a_sentence_not_a_stack_trace(
    client: TestClient, repository: Repository, make_tender
) -> None:
    tender = _loaded(repository, make_tender, count=1)[0]
    _as(client)

    response = client.post(
        f"/register/{tender.id}/review", data={"verdict": "relevant", "bid_route": "maybe"}
    )

    assert response.status_code == 400
    assert "is not a bid route" in response.text


# ----------------------------------------------------------------- navigation


def test_previous_and_next_stay_inside_the_filter_you_arrived_from(
    client: TestClient, repository: Repository, make_tender
) -> None:
    """Review is a flow, not a series of round trips back to the list."""
    _loaded(repository, make_tender, count=3)
    listing = client.get("/register?band=all&past=1&mode=table").text

    assert 'data-detail="/register/notice/ted:1-2026?' in listing

    second = client.get("/register/notice/ted:2-2026?band=all&past=1&cursor=1").text

    assert "/register/notice/ted:1-2026?" in second
    assert "/register/notice/ted:3-2026?" in second
    assert "2 of 3 in this view" in second


def test_the_first_notice_has_no_previous_and_the_last_has_no_next(
    client: TestClient, repository: Repository, make_tender
) -> None:
    _loaded(repository, make_tender, count=2)

    first = client.get("/register/notice/ted:1-2026?band=all&past=1&cursor=0").text
    last = client.get("/register/notice/ted:2-2026?band=all&past=1&cursor=1").text

    assert 'id="previous-notice"' not in first
    assert 'id="next-notice"' in first
    assert 'id="previous-notice"' in last
    assert 'id="next-notice"' not in last


def test_a_narrower_filter_gives_different_neighbours(
    client: TestClient, repository: Repository, make_tender
) -> None:
    tenders = _loaded(repository, make_tender, count=3)
    narrowed = tenders[1].model_copy(update={"buyer_country": "DEU"})
    repository.upsert_tenders([narrowed], run_id=RUN)

    page = client.get("/register/notice/ted:2-2026?band=all&past=1&buyer_country=DEU&cursor=0").text

    assert "1 of 1 in this view" in page
    assert 'id="previous-notice"' not in page
    assert 'id="next-notice"' not in page


def test_a_detail_page_reached_with_an_unreadable_filter_still_shows_the_notice(
    client: TestClient, repository: Repository, make_tender
) -> None:
    _loaded(repository, make_tender, count=1)

    response = client.get("/register/notice/ted:1-2026?sort=nonsense")

    assert response.status_code == 200
    assert 'id="next-notice"' not in response.text
