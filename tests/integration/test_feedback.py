"""Feedback mode: off by default, safe when on, and removable without a trace.

The rule that makes it usable is that an intercepted click does **not** run. A
colleague in feedback mode who clicks "Update from TED" is leaving a comment about
that button, not starting an hour-long ingest.

The rule that makes it safe is that only an element's identifier and its visible
label are stored. The contents of an input never are.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from watchdog.storage.repository import Repository
from watchdog.web.routes.feedback import COOKIE


def test_feedback_mode_is_off_by_default(client: TestClient) -> None:
    html = client.get("/register").text

    assert 'data-feedback="off"' in html
    assert "Feedback mode: off" in html


def test_turning_it_on_sets_the_cookie_and_says_what_it_does(client: TestClient) -> None:
    response = client.post("/feedback/mode", data={"on": "1"})

    assert response.status_code == 200
    assert client.cookies.get(COOKIE) == "on"
    assert "Feedback mode: on" in response.text
    assert "do nothing else" in response.text
    assert 'data-feedback="on"' in client.get("/register").text


def test_turning_it_off_keeps_every_comment(client: TestClient, repository: Repository) -> None:
    client.post("/register/reviewer", data={"name": "Ada Lovelace"})
    client.post("/feedback/mode", data={"on": "1"})
    client.post(
        "/feedback",
        data={
            "page": "/register",
            "element": "#update-button",
            "element_label": "Update from TED",
            "comment": "This should say how far back it reads.",
        },
    )

    client.post("/feedback/mode", data={"on": "0"})

    assert 'data-feedback="off"' in client.get("/register").text
    assert len(repository.list_feedback()) == 1


def test_a_comment_records_what_was_clicked_and_who_said_it(
    client: TestClient, repository: Repository
) -> None:
    client.post("/register/reviewer", data={"name": "Ada Lovelace"})

    response = client.post(
        "/feedback",
        data={
            "page": "/register/notice/ted:1-2026",
            "element": "#save-review",
            "element_label": "Save decision",
            "tender_id": "ted:1-2026",
            "comment": "The owner box should remember the last person.",
        },
    )

    assert response.status_code == 200
    stored = repository.list_feedback()[0]
    assert stored.page == "/register/notice/ted:1-2026"
    assert stored.element == "#save-review"
    assert stored.element_label == "Save decision"
    assert stored.tender_id == "ted:1-2026"
    assert stored.reported_by == "Ada Lovelace"


def test_an_empty_comment_is_refused_rather_than_stored_as_a_blank_row(
    client: TestClient, repository: Repository
) -> None:
    response = client.post(
        "/feedback",
        data={"page": "/register", "element": "#x", "element_label": "X", "comment": "   "},
    )

    assert response.status_code == 400
    assert "Write something in the box" in response.text
    assert repository.list_feedback() == []


def test_a_comment_is_escaped_exactly_as_source_and_model_text_is(
    client: TestClient, repository: Repository
) -> None:
    client.post(
        "/feedback",
        data={
            "page": "/register",
            "element": "#x",
            "element_label": "<img src=x onerror=alert(1)>",
            "comment": "<script>alert('hi')</script>",
        },
    )

    html = client.get("/feedback").text

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
    assert "onerror=alert(1)>" not in html


def test_the_listing_groups_by_page_and_by_what_was_clicked(
    client: TestClient, repository: Repository
) -> None:
    for element, comment in [
        ("#update-button", "Say how far back it reads."),
        ("#update-button", "And how long it will take."),
        ("#rescreen-stale", "Should say how many."),
    ]:
        client.post(
            "/feedback",
            data={
                "page": "/settings",
                "element": element,
                "element_label": element.strip("#"),
                "comment": comment,
            },
        )

    html = client.get("/feedback").text

    assert "3 comments" in html
    assert "Say how far back it reads." in html
    assert "Should say how many." in html


def test_the_export_is_markdown_streamed_as_an_attachment(
    client: TestClient, repository: Repository
) -> None:
    client.post(
        "/feedback",
        data={
            "page": "/register",
            "element": "#update-button",
            "element_label": "Update from TED",
            "comment": "Needs a progress figure.",
        },
    )

    response = client.get("/feedback/export.md")

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.text.startswith("# Watchdog interface feedback")
    assert "Update from TED" in response.text
    assert "Needs a progress figure." in response.text


def test_the_comment_table_is_its_own_and_holds_nothing_about_a_procurement(
    client: TestClient, repository: Repository
) -> None:
    """A comment is about Watchdog. It is not a document extract or a review."""
    from watchdog.storage.tables import FeedbackCommentRow

    columns = {column.name for column in FeedbackCommentRow.__table__.columns}

    assert columns == {
        "id",
        "page",
        "element",
        "element_label",
        "tender_id",
        "comment",
        "reported_by",
        "created_at",
    }


def test_the_intercepting_script_never_sends_the_contents_of_a_field() -> None:
    """The browser half of the same rule, checked where it is written.

    A comment box that recorded what was in the field beside it would, sooner or
    later, record a password.
    """
    from watchdog.web.templating import STATIC_DIR

    source = (STATIC_DIR / "js" / "feedback.js").read_text(encoding="utf-8")

    assert "element.value" not in source.replace("Deliberately not element.value", "")
    assert "preventDefault" in source
    assert "stopImmediatePropagation" in source
