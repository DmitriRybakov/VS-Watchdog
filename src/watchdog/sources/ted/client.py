"""The TED search client. Sequential, paced, and loud when a page is incomplete.

Everything here was shaped by what the live endpoint actually does:

- ``fields`` is mandatory, and one unrecognised name fails the whole request with
  a 400 that lists every supported name but does not say which of ours was wrong.
  ``unknown_fields`` finds it by diffing that list locally, in one request.
- ``limit`` may not exceed 250.
- A 429 arrives as **HTML from nginx** with no ``Retry-After``. Nothing here
  assumes an error body is JSON, and the rate-limit backoff starts at 30 seconds
  because there is no header to honour.
- The envelope carries ``timedOut``. A timed-out page is an incomplete page even
  though the HTTP call succeeded, so it is retried and then raised. It must never
  be mistaken for "no more results".
- The only correlation id the response carries is CloudFront's ``x-amz-cf-id``;
  there is no ``x-request-id``.

Notice bodies are never logged. Counts, statuses and durations are.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterator, Sequence
from types import TracebackType
from typing import Any

import httpx
from tenacity import RetryCallState, Retrying, retry_if_exception_type, stop_after_attempt

from watchdog.core.logging import get_logger
from watchdog.sources.errors import InvalidPayloadError, QueryError, TransportError

log = get_logger(__name__)

TED_SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"

# Worth trying again: the request may succeed unchanged a moment later.
RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

# TED rejects anything larger outright.
MAX_PAGE_SIZE = 250

# The marker in the 400 body that precedes the full list of valid field names.
_SUPPORTED_VALUES_MARKER = "supported values are:"

_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}


class _Retryable(Exception):
    """Internal: this attempt failed in a way that is worth repeating."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retry_after: float | None = None,
        request_id: str | None = None,
        incomplete_page: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.request_id = request_id
        # True when TED answered 200 but told us the page is not the whole answer.
        self.incomplete_page = incomplete_page

    def as_public_error(self, attempts: int) -> Exception:
        if self.incomplete_page:
            return InvalidPayloadError(
                f"{self} after {attempts} attempts; the window is incomplete "
                "and the watermark must not advance",
                status=self.status,
            )
        return TransportError(
            f"{self} after {attempts} attempts",
            status=self.status,
            attempts=attempts,
            request_id=self.request_id,
        )


class TedClient:
    """Talks to TED. One request at a time, with a pause between pages."""

    def __init__(
        self,
        *,
        url: str = TED_SEARCH_URL,
        connect_timeout: float = 10.0,
        read_timeout: float = 60.0,
        write_timeout: float = 30.0,
        max_attempts: int = 5,
        backoff_initial: float = 1.0,
        backoff_max: float = 120.0,
        rate_limit_backoff: float = 30.0,
        page_pause: float = 1.0,
        max_pages: int = 1000,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._url = url
        self._max_attempts = max(1, max_attempts)
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._rate_limit_backoff = rate_limit_backoff
        self._page_pause = page_pause
        self._max_pages = max_pages
        self._sleep = sleep
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(
                connect=connect_timeout,
                read=read_timeout,
                write=write_timeout,
                pool=connect_timeout,
            )
        )

    # ------------------------------------------------------------------ lifetime

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> TedClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -------------------------------------------------------------------- public

    def validate_query(
        self, query: str, fields: Sequence[str], *, run_id: str | None = None
    ) -> None:
        """Ask TED to check the query without running it.

        ``checkQuerySyntax`` is a validate-only mode: it answers 200 with no
        notices and a null total. A malformed query is a 400 either way, so this
        turns a configuration mistake into an immediate, named error instead of an
        empty result set nobody notices.
        """
        self._request(
            {
                "query": query,
                "fields": list(fields),
                "limit": 1,
                "page": 1,
                "checkQuerySyntax": True,
            },
            run_id=run_id,
            purpose="validate_query",
        )

    def count_notices(
        self,
        query: str,
        fields: Sequence[str],
        *,
        run_id: str | None = None,
        only_latest_versions: bool = True,
    ) -> int:
        """How many notices the query matches, without reading them.

        One request with ``limit: 1``: the envelope's ``totalNoticeCount`` is the
        whole result set, not the page. This is what lets a truncated listing say
        what it left out instead of looking like the complete answer.

        ``onlyLatestVersions`` must match what the caller then iterates, or the
        count describes a different result set. Measured 5-12 September 2026: 217
        without it against 212 with it, the difference being superseded versions.
        """
        envelope = self._request(
            {
                "query": query,
                "fields": list(fields),
                "limit": 1,
                "page": 1,
                "onlyLatestVersions": only_latest_versions,
            },
            run_id=run_id,
            purpose="count",
        )
        total = envelope.get("totalNoticeCount")
        if not isinstance(total, int):
            raise InvalidPayloadError(f"expected 'totalNoticeCount' to be a number, got {total!r}")
        return total

    def unknown_fields(self, fields: Sequence[str], *, run_id: str | None = None) -> list[str]:
        """Which of ``fields`` TED does not recognise. Empty means all are valid.

        TED's 400 lists every name it supports but not which of ours was wrong, so
        the diff happens here rather than by probing one field at a time.
        """
        try:
            self._request(
                {
                    "query": "publication-date>=today(-1)",
                    "fields": list(fields),
                    "limit": 1,
                    "page": 1,
                    "checkQuerySyntax": True,
                },
                run_id=run_id,
                purpose="validate_fields",
            )
        except QueryError as exc:
            supported = _supported_field_names(str(exc))
            if supported is None:
                raise
            return [field for field in fields if field not in supported]

        return []

    def iter_notices(
        self,
        query: str,
        fields: Sequence[str],
        *,
        run_id: str | None = None,
        page_size: int = MAX_PAGE_SIZE,
        only_latest_versions: bool = True,
    ) -> Iterator[dict[str, Any]]:
        """Every notice the query matches, following the iteration token to the end.

        Raises rather than stopping early. A short list that looks complete is the
        failure this whole module is arranged to prevent.
        """
        if not fields:
            raise QueryError("the field list is empty; TED requires at least one field")
        if page_size > MAX_PAGE_SIZE:
            raise QueryError(f"page size {page_size} exceeds TED's maximum of {MAX_PAGE_SIZE}")

        started = time.monotonic()
        token: str | None = None
        pages = 0
        yielded = 0

        while True:
            body: dict[str, Any] = {
                "query": query,
                "fields": list(fields),
                "limit": page_size,
                "paginationMode": "ITERATION",
                "onlyLatestVersions": only_latest_versions,
            }
            if token is not None:
                body["iterationNextToken"] = token

            envelope = self._request(body, run_id=run_id, purpose="search")
            notices = envelope.get("notices")
            if notices is None:
                notices = []
            if not isinstance(notices, list):
                raise InvalidPayloadError(
                    f"expected 'notices' to be a list, got {type(notices).__name__}"
                )

            pages += 1
            for notice in notices:
                if not isinstance(notice, dict):
                    raise InvalidPayloadError(
                        f"expected each notice to be an object, got {type(notice).__name__}"
                    )
                yielded += 1
                yield notice

            next_token = envelope.get("iterationNextToken")
            if not next_token or not notices:
                break
            if next_token == token:
                raise InvalidPayloadError(
                    "the iteration token did not advance; the window would be read forever"
                )
            if pages >= self._max_pages:
                raise InvalidPayloadError(
                    f"stopped after {pages} pages, which is more than this window should need"
                )

            token = str(next_token)
            self._sleep(self._page_pause)

        log.info(
            "ted_search_complete",
            run_id=run_id,
            pages=pages,
            notices=yielded,
            total_reported=envelope.get("totalNoticeCount"),
            duration_ms=round((time.monotonic() - started) * 1000),
        )

    # ----------------------------------------------------------------- internals

    def _request(self, body: dict[str, Any], *, run_id: str | None, purpose: str) -> dict[str, Any]:
        """One logical request, retried inside its own budget."""
        retrying = Retrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=self._wait,
            retry=retry_if_exception_type(_Retryable),
            sleep=self._sleep,
            reraise=True,
        )

        try:
            return retrying(self._attempt, body, run_id=run_id, purpose=purpose)
        except _Retryable as exc:
            log.error(
                "ted_request_failed",
                run_id=run_id,
                purpose=purpose,
                status=exc.status,
                attempts=self._max_attempts,
                request_id=exc.request_id,
                reason=str(exc),
            )
            raise exc.as_public_error(self._max_attempts) from exc

    def _attempt(self, body: dict[str, Any], *, run_id: str | None, purpose: str) -> dict[str, Any]:
        started = time.monotonic()

        try:
            response = self._client.post(self._url, json=body, headers=_HEADERS)
        except httpx.TimeoutException as exc:
            raise _Retryable(f"{purpose}: {type(exc).__name__}") from exc
        except httpx.TransportError as exc:
            raise _Retryable(f"{purpose}: {type(exc).__name__}: {exc}") from exc

        duration_ms = round((time.monotonic() - started) * 1000)
        request_id = response.headers.get("x-amz-cf-id")
        content_type = response.headers.get("content-type", "")
        status = response.status_code

        if status in RETRYABLE_STATUSES:
            log.warning(
                "ted_request_retryable",
                run_id=run_id,
                purpose=purpose,
                status=status,
                content_type=content_type,
                request_id=request_id,
                duration_ms=duration_ms,
            )
            raise _Retryable(
                f"{purpose}: HTTP {status}",
                status=status,
                retry_after=_retry_after_seconds(response),
                request_id=request_id,
            )

        if status >= 400:
            message, field = _error_detail(response)
            log.error(
                "ted_request_rejected",
                run_id=run_id,
                purpose=purpose,
                status=status,
                content_type=content_type,
                request_id=request_id,
                field=field,
                duration_ms=duration_ms,
            )
            if status == 400:
                raise QueryError(
                    f"TED rejected the request: {message}",
                    field=field,
                    query=str(body.get("query")),
                )
            raise InvalidPayloadError(
                f"{purpose}: HTTP {status} from the search endpoint: {message}",
                status=status,
                content_type=content_type,
            )

        envelope = _json_object(response, purpose)

        if envelope.get("timedOut") is True:
            log.warning(
                "ted_page_timed_out",
                run_id=run_id,
                purpose=purpose,
                request_id=request_id,
                duration_ms=duration_ms,
            )
            raise _Retryable(
                f"{purpose}: TED reported the page as timed out",
                status=status,
                request_id=request_id,
                incomplete_page=True,
            )

        notices = envelope.get("notices")
        log.info(
            "ted_request_ok",
            run_id=run_id,
            purpose=purpose,
            status=status,
            request_id=request_id,
            notices=len(notices) if isinstance(notices, list) else 0,
            total=envelope.get("totalNoticeCount"),
            duration_ms=duration_ms,
        )
        return envelope

    def _wait(self, retry_state: RetryCallState) -> float:
        """Bounded exponential backoff with jitter; longer when rate limited."""
        exc = retry_state.outcome.exception() if retry_state.outcome is not None else None
        attempt = max(1, int(retry_state.attempt_number))

        if isinstance(exc, _Retryable):
            if exc.retry_after is not None:
                return min(exc.retry_after, self._backoff_max)
            base = self._rate_limit_backoff if exc.status == 429 else self._backoff_initial
        else:
            base = self._backoff_initial

        delay: float = min(base * (2 ** (attempt - 1)), self._backoff_max)
        return delay + random.uniform(0.0, delay * 0.25)


# ------------------------------------------------------------------- parsing


def _json_object(response: httpx.Response, purpose: str) -> dict[str, Any]:
    """The response body as an object, never assuming it is JSON because it said so."""
    content_type = response.headers.get("content-type", "")
    if "json" not in content_type.lower():
        raise InvalidPayloadError(
            f"{purpose}: expected JSON, got {content_type or 'no content-type'}",
            status=response.status_code,
            content_type=content_type,
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise InvalidPayloadError(
            f"{purpose}: the body claimed to be JSON but could not be parsed",
            status=response.status_code,
            content_type=content_type,
        ) from exc

    if not isinstance(data, dict):
        raise InvalidPayloadError(
            f"{purpose}: expected an object, got {type(data).__name__}",
            status=response.status_code,
            content_type=content_type,
        )
    return data


def _error_detail(response: httpx.Response) -> tuple[str, str | None]:
    """(message, offending field) from an error body of unknown shape."""
    content_type = response.headers.get("content-type", "").lower()
    if "json" not in content_type:
        return (f"non-JSON body ({content_type or 'no content-type'})", None)

    try:
        data = response.json()
    except ValueError:
        return ("a body that claimed to be JSON but could not be parsed", None)

    if not isinstance(data, dict):
        return (f"a JSON {type(data).__name__} rather than an object", None)

    message = str(data.get("message") or "").strip()
    error = data.get("error")
    field: str | None = None

    if isinstance(error, dict):
        field = _as_optional_str(error.get("fieldName"))
    elif isinstance(error, list) and error and isinstance(error[0], dict):
        field = _as_optional_str(error[0].get("field"))
        if not message:
            message = str(error[0].get("message") or "").strip()

    return (message or "no message", field)


def _as_optional_str(value: Any) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """The Retry-After header in seconds. TED does not send one; other proxies might."""
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw.strip()))
    except ValueError:
        return None


def _supported_field_names(message: str) -> frozenset[str] | None:
    """The field names out of TED's "unsupported value" error, or None if absent."""
    marker = message.find(_SUPPORTED_VALUES_MARKER)
    if marker < 0:
        return None

    listed = message[marker + len(_SUPPORTED_VALUES_MARKER) :].strip().rstrip(")")
    names = {name.strip() for name in listed.split(",")}
    names.discard("")
    return frozenset(names) or None
