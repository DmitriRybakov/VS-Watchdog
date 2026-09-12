"""Errors a source adapter raises. Typed, so a caller can tell them apart.

The distinction that matters is whether the notice is lost. A transport failure
means try again later and the window is incomplete. A mapping failure means this
one notice cannot be understood: it is quarantined with its identifier, and the
rest of the page still lands. Nothing is ever silently dropped.
"""

from __future__ import annotations


class SourceError(Exception):
    """Base class for everything a source adapter raises."""


class TransportError(SourceError):
    """Could not get an answer: connection, timeout, rate limit, or a 5xx.

    Retrying is reasonable. ``status`` is None when the request never completed.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        attempts: int = 1,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.attempts = attempts
        self.request_id = request_id


class InvalidPayloadError(SourceError):
    """The source answered, but not in a shape we understand.

    Raised for a body that is not JSON, a missing envelope key, or a page the
    source itself reported as incomplete. Retrying the same request will not help
    unless the cause was the source timing out.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        content_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.content_type = content_type


class QueryError(SourceError):
    """The search query or the requested field list was rejected.

    A configuration problem, not a runtime one, and it must stop the run
    immediately: the alternative is a query that quietly returns nothing.
    """

    def __init__(self, message: str, *, field: str | None = None, query: str | None = None) -> None:
        super().__init__(message)
        self.field = field
        self.query = query


class MappingError(SourceError):
    """One notice could not be turned into a Tender.

    Carries the source's own identifier so the caller can quarantine it by name
    and a person can go and look at the notice.
    """

    def __init__(self, message: str, *, source_id: str | None = None) -> None:
        super().__init__(message)
        self.source_id = source_id

    def __str__(self) -> str:
        base = super().__str__()
        return f"{base} (source_id={self.source_id})" if self.source_id else base
