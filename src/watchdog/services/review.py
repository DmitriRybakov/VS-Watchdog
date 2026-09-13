"""Recording what a colleague decided. The only path by which a verdict is written.

A review is human judgement, one of the four kinds of provenance this tool keeps
apart, and nothing automated may produce one. That is why this is its own module
with one entry point rather than a method on the screening service: there is no
code path here that a screening run could reach.

Append-only. A changed mind supersedes the previous review and never edits it,
because why somebody changed their mind is worth keeping, and the earlier verdict,
note, author and timestamp stay exactly as they were written.

**A field nobody filled in is carried forward, not blanked.** Recording ``y`` from
the triage queue says something about the verdict and nothing about the bid route,
and writing "not assessed" over a route a colleague chose last week would be a
loss disguised as a decision. So an argument left as ``None`` keeps whatever the
previous review said; clearing a field is done by passing an empty string, which
is a different thing from not mentioning it.

``reviewed_by`` has **no default**. There is no sign-in yet, so the name comes
from what a colleague typed into the register, and the temptation is to fall back
to some constant when nothing was typed. That constant would be written onto
every review made before authentication exists, and those reviews are the set
later work gets measured against - a set where every row says the same thing
measures nothing. An unattributed review is lost data, so it is refused here
rather than quietly given a placeholder.
"""

from __future__ import annotations

from watchdog.core.enums import BidRoute, Verdict
from watchdog.core.logging import get_logger
from watchdog.core.models import Review
from watchdog.storage.db import get_session_factory
from watchdog.storage.repository import Repository

log = get_logger(__name__)

# How long a free-text field may be. Generous, but not a document store.
MAX_NOTE = 4000
MAX_NEXT_ACTION = 500
MAX_OWNER = 128


class UnknownTender(LookupError):
    """A verdict for a notice the register does not hold."""


class UnattributedReview(ValueError):
    """A verdict with nobody's name on it. Refused, never defaulted."""


class InvalidReview(ValueError):
    """A review Watchdog will not store. Shown as a plain sentence."""


def record_verdict(
    tender_id: str,
    verdict: Verdict,
    *,
    reviewed_by: str,
    owner: str | None = None,
    note: str | None = None,
    bid_route: BidRoute | None = None,
    next_action: str | None = None,
    repository: Repository | None = None,
) -> Review:
    """Record one decision against one notice, in somebody's name.

    Checks the notice exists first, so a mistyped id is a plain "we do not hold
    that notice" rather than a foreign-key error from the database.
    """
    name = " ".join((reviewed_by or "").split())
    if not name:
        raise UnattributedReview(
            "A decision has to be recorded in somebody's name. "
            "Put yours in the 'Recording as' box at the top of the register."
        )

    store = repository or Repository(get_session_factory())

    if store.get_tender(tender_id) is None:
        raise UnknownTender(f"the register holds no notice with the id {tender_id!r}")

    previous = store.get_review(tender_id)
    previous_route = previous.bid_route if previous else BidRoute.NOT_ASSESSED

    review = store.save_review(
        Review(
            tender_id=tender_id,
            verdict=verdict,
            bid_route=previous_route if bid_route is None else bid_route,
            owner=_text(owner, previous.owner if previous else None, MAX_OWNER, "Owner"),
            next_action=_text(
                next_action,
                previous.next_action if previous else None,
                MAX_NEXT_ACTION,
                "Next action",
            ),
            note=_text(note, previous.note if previous else None, MAX_NOTE, "Note"),
            reviewed_by=name,
        )
    )
    log.info(
        "verdict_recorded",
        tender_id=tender_id,
        verdict=verdict.value,
        bid_route=review.bid_route.value,
        reviewed_by=name,
    )
    return review


def history(tender_id: str, *, repository: Repository | None = None) -> list[Review]:
    """Every review of one notice, newest first, superseded ones included."""
    store = repository or Repository(get_session_factory())
    return store.list_reviews(tender_id)


def _text(chosen: str | None, previous: str | None, limit: int, label: str) -> str | None:
    """A submitted value, the previous one, or None. An empty string clears it."""
    if chosen is None:
        return previous
    text = chosen.strip()
    if not text:
        return None
    if len(text) > limit:
        raise InvalidReview(f"{label} can be at most {limit} characters; that one is longer.")
    return text
