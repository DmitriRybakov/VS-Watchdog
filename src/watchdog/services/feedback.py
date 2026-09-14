"""Comments a colleague leaves about the interface, kept apart from tender data.

Feedback mode is a toggle in the header. While it is on, clicking anything opens
a comment box and the click captures what was clicked - the element and its
visible label - so nothing has to be added to individual templates. That is the
whole point of doing it this way: comment icons beside every control would be
dozens of template edits to add and the same number to strip out afterwards.

Two things this module refuses to store, deliberately:

- **the contents of any input.** A comment box that recorded what was in the field
  beside it would, sooner or later, record a password. The browser sends a label
  and an identifier and nothing else, and the sanitising here is the second line
  rather than the first.
- **anything about the notice beyond its id.** A comment is about Watchdog. It is
  not a document extract, not an assessment and not a review, and the separate
  table is what keeps it from ever being read as one.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass

from watchdog.core.clock import utc_now
from watchdog.core.logging import get_logger
from watchdog.core.models import FeedbackComment
from watchdog.storage.db import get_session_factory
from watchdog.storage.repository import Repository

log = get_logger(__name__)

# Long enough for a paragraph of detail, short enough that nobody pastes a
# document into it.
MAX_COMMENT = 2000
MAX_LABEL = 200
MAX_ELEMENT = 200
MAX_PAGE = 300

# Control characters, which none of these fields contains and a log line should
# not. Newlines are kept in the comment itself and stripped everywhere else.
_FORBIDDEN = frozenset(chr(code) for code in range(0x20)) - {"\n"} | {"\x7f"}


class EmptyComment(ValueError):
    """A comment with nothing in it. Refused rather than stored as a blank row."""


class NamelessComment(ValueError):
    """A comment nobody can be asked about. Refused rather than stored anonymously.

    Every one of these is a question for whoever left it - what did you expect to
    see, what were you trying to do. A row saying "Name not recorded" cannot be
    followed up, so it is worth less than the interruption of asking.
    """


@dataclass(frozen=True)
class ElementGroup:
    """Every comment left on one element of one page."""

    element: str
    label: str | None
    comments: tuple[FeedbackComment, ...]


@dataclass(frozen=True)
class PageGroup:
    """Every comment left on one page, grouped by what was clicked."""

    page: str
    elements: tuple[ElementGroup, ...]

    @property
    def count(self) -> int:
        return sum(len(group.comments) for group in self.elements)


def record(
    *,
    page: str,
    element: str,
    element_label: str | None,
    comment: str,
    tender_id: str | None = None,
    reported_by: str | None = None,
    repository: Repository | None = None,
) -> FeedbackComment:
    """Store one comment. Raises when there is nothing to store."""
    text = (comment or "").strip()
    if not text:
        raise EmptyComment("Write something in the box before saving the comment.")

    who = _clean(reported_by, 128)
    if not who:
        raise NamelessComment(
            "Please put your name in before saving, so somebody can ask you about this."
        )

    store = repository or Repository(get_session_factory())
    saved = store.save_feedback(
        FeedbackComment(
            page=_clean(page, MAX_PAGE) or "(page not recorded)",
            element=_clean(element, MAX_ELEMENT) or "(element not recorded)",
            element_label=_clean(element_label, MAX_LABEL),
            tender_id=_clean(tender_id, 255),
            comment=_trim(text, MAX_COMMENT),
            reported_by=who,
            created_at=utc_now(),
        )
    )
    log.info("feedback_recorded", page=saved.page, element=saved.element)
    return saved


def grouped(*, repository: Repository | None = None) -> list[PageGroup]:
    """Every comment, grouped by page and then by what was clicked."""
    store = repository or Repository(get_session_factory())
    comments = store.list_feedback()

    by_page: dict[str, dict[str, list[FeedbackComment]]] = defaultdict(lambda: defaultdict(list))
    labels: dict[tuple[str, str], str | None] = {}
    for comment in comments:
        by_page[comment.page][comment.element].append(comment)
        labels.setdefault((comment.page, comment.element), comment.element_label)

    return [
        PageGroup(
            page=page,
            elements=tuple(
                ElementGroup(
                    element=element,
                    label=labels.get((page, element)),
                    comments=tuple(items),
                )
                for element, items in sorted(elements.items())
            ),
        )
        for page, elements in sorted(by_page.items())
    ]


def to_markdown(pages: list[PageGroup]) -> Iterator[str]:
    """The comments as a Markdown document, streamed. Never written to disk.

    Markdown rather than a spreadsheet because the thing this export is for is
    being handed to somebody - or something - that has to read the sentences.
    """
    yield "# Watchdog interface feedback\n\n"
    if not pages:
        yield "No comments have been recorded.\n"
        return

    total = sum(page.count for page in pages)
    yield f"{total} comment(s) across {len(pages)} page(s). All times UTC.\n"

    for page in pages:
        yield f"\n## {page.page}\n"
        for element in page.elements:
            heading = element.label or element.element
            yield f"\n### {heading}\n"
            yield f"\n- element: `{element.element}`\n"
            for comment in element.comments:
                who = comment.reported_by or "not recorded"
                when = comment.created_at.strftime("%Y-%m-%d %H:%M")
                notice = f" (notice {comment.tender_id})" if comment.tender_id else ""
                yield f"- **{who}**, {when} UTC{notice}\n"
                for line in comment.comment.splitlines():
                    yield f"  > {line}\n"


def _clean(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())
    text = "".join(character for character in text if character not in _FORBIDDEN)
    return _trim(text, limit) or None


def _trim(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit]
