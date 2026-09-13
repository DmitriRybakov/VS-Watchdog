"""Recording what a colleague decided. The only path by which a verdict is written.

A review is human judgement, one of the four kinds of provenance this tool keeps
apart, and nothing automated may produce one. That is why this is its own module
with one entry point rather than a method on the screening service: there is no
code path here that a screening run could reach.

Append-only. A changed mind supersedes the previous review and never edits it,
because why somebody changed their mind is worth keeping.

``reviewed_by`` has **no default**. There is no sign-in yet, so the name comes
from what a colleague typed into the register, and the temptation is to fall back
to some constant when nothing was typed. That constant would be written onto
every review made before authentication exists, and those reviews are the set
later work gets measured against - a set where every row says the same thing
measures nothing. An unattributed review is lost data, so it is refused here
rather than quietly given a placeholder.
"""

from __future__ import annotations

from watchdog.core.enums import Verdict
from watchdog.core.logging import get_logger
from watchdog.core.models import Review
from watchdog.storage.db import get_session_factory
from watchdog.storage.repository import Repository

log = get_logger(__name__)


class UnknownTender(LookupError):
    """A verdict for a notice the register does not hold."""


class UnattributedReview(ValueError):
    """A verdict with nobody's name on it. Refused, never defaulted."""


def record_verdict(
    tender_id: str,
    verdict: Verdict,
    *,
    reviewed_by: str,
    owner: str | None = None,
    note: str | None = None,
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

    review = store.save_review(
        Review(
            tender_id=tender_id,
            verdict=verdict,
            owner=owner,
            note=note,
            reviewed_by=name,
        )
    )
    log.info("verdict_recorded", tender_id=tender_id, verdict=verdict.value, reviewed_by=name)
    return review
