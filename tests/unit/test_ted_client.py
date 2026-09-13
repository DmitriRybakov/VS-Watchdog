"""The TED client: pagination, retries, and refusing to call an incomplete page complete.

No test here touches the network. The response shapes are the ones the live API
actually produced, including the HTML 429 body and the `timedOut` envelope flag.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from watchdog.sources.errors import InvalidPayloadError, QueryError, TransportError
from watchdog.sources.ted.client import TED_SEARCH_URL, TedClient
from watchdog.sources.ted.config import (
    MAX_FIELDS_PER_PAGE,
    fields_per_page,
    load_ted_config,
    max_page_size,
)
from watchdog.sources.ted.mapper import REQUESTED_FIELDS

FIELDS = ("publication-number", "notice-title")

# nginx answers a 429 with HTML and no Retry-After. Verified against the live API.
HTML_429 = (
    "<html>\r\n<head><title>429 Too Many Requests</title></head>\r\n"
    "<body>\r\n<center><h1>429 Too Many Requests</h1></center>\r\n"
    "<hr><center>nginx/1.31.3</center>\r\n</body>\r\n</html>\r\n"
)


def page(notices: list[dict[str, Any]], token: str | None, total: int = 0) -> dict[str, Any]:
    return {
        "notices": notices,
        "totalNoticeCount": total or len(notices),
        "iterationNextToken": token,
        "timedOut": False,
    }


def notice(number: str) -> dict[str, Any]:
    return {"publication-number": number}


@pytest.fixture
def client() -> TedClient:
    """No real waiting: the sleep function is injected, so retries are instant."""
    return TedClient(max_attempts=3, page_pause=0.0, sleep=lambda _seconds: None)


# --------------------------------------------------------------- pagination


@respx.mock
def test_iteration_follows_the_token_to_the_end(client: TedClient) -> None:
    route = respx.post(TED_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=page([notice("1-2026"), notice("2-2026")], "TOKEN-A", 5)),
            httpx.Response(200, json=page([notice("3-2026"), notice("4-2026")], "TOKEN-B", 5)),
            httpx.Response(200, json=page([notice("5-2026")], None, 5)),
        ]
    )

    numbers = [n["publication-number"] for n in client.iter_notices("q", FIELDS)]

    assert numbers == ["1-2026", "2-2026", "3-2026", "4-2026", "5-2026"]
    assert route.call_count == 3


@respx.mock
def test_the_token_is_sent_back_on_the_next_request(client: TedClient) -> None:
    route = respx.post(TED_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=page([notice("1-2026")], "TOKEN-A")),
            httpx.Response(200, json=page([notice("2-2026")], None)),
        ]
    )

    list(client.iter_notices("q", FIELDS))

    first = respx.calls[0].request.read().decode()
    second = respx.calls[1].request.read().decode()

    assert "iterationNextToken" not in first
    assert "TOKEN-A" in second
    assert '"paginationMode": "ITERATION"' in second.replace(
        '"paginationMode":"ITERATION"', '"paginationMode": "ITERATION"'
    )
    assert route.call_count == 2


@respx.mock
def test_only_latest_versions_is_requested(client: TedClient) -> None:
    respx.post(TED_SEARCH_URL).mock(return_value=httpx.Response(200, json=page([], None)))

    list(client.iter_notices("q", FIELDS))

    assert "onlyLatestVersions" in respx.calls[0].request.read().decode()


@respx.mock
def test_counting_asks_for_the_same_result_set_it_will_be_compared_against(
    client: TedClient,
) -> None:
    # Without onlyLatestVersions the count includes superseded versions: 217
    # against 212 over 5-12 September 2026. A count of a different result set is
    # worse than no count, because it looks authoritative.
    respx.post(TED_SEARCH_URL).mock(return_value=httpx.Response(200, json=page([], None, 212)))

    assert client.count_notices("q", FIELDS) == 212

    body = respx.calls[0].request.read().decode()
    assert "onlyLatestVersions" in body
    assert "true" in body.split("onlyLatestVersions")[1][:10]


@respx.mock
def test_a_count_that_is_not_a_number_is_refused(client: TedClient) -> None:
    respx.post(TED_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"notices": [], "totalNoticeCount": None})
    )

    with pytest.raises(InvalidPayloadError):
        client.count_notices("q", FIELDS)


@respx.mock
def test_a_final_empty_page_with_a_token_still_terminates(client: TedClient) -> None:
    # The live API does this: the last page is empty but still carries a token.
    respx.post(TED_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=page([notice("1-2026")], "TOKEN-A")),
            httpx.Response(200, json=page([], "TOKEN-B")),
        ]
    )

    assert [n["publication-number"] for n in client.iter_notices("q", FIELDS)] == ["1-2026"]


@respx.mock
def test_a_token_that_does_not_advance_is_an_error_not_a_loop(client: TedClient) -> None:
    respx.post(TED_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=page([notice("1-2026")], "SAME"))
    )

    with pytest.raises(InvalidPayloadError, match="did not advance"):
        list(client.iter_notices("q", FIELDS))


@respx.mock
def test_a_page_size_above_the_maximum_is_refused_before_any_request(client: TedClient) -> None:
    with pytest.raises(QueryError, match="250"):
        list(client.iter_notices("q", FIELDS, page_size=251))

    assert respx.calls.call_count == 0


@respx.mock
def test_a_projection_over_the_fields_cap_is_refused_before_any_request(
    client: TedClient,
) -> None:
    # TED prices a request as fields per page and refuses one over 10,000 with a
    # 400 on the very first call. Caught here instead, so the failure names a page
    # size that would work rather than arriving as a rejected query mid-run.
    many = tuple(f"field-{index}" for index in range(55))

    with pytest.raises(QueryError) as caught:
        list(client.iter_notices("q", many, page_size=250))

    assert respx.calls.call_count == 0
    assert "fields per page" in str(caught.value)
    assert "175" in str(caught.value), "it says what page size would fit"


@respx.mock
def test_the_shipped_field_list_and_page_size_are_allowed_through(client: TedClient) -> None:
    # The pair that actually ships has to pass its own guard, or every run fails.
    respx.post(TED_SEARCH_URL).mock(return_value=httpx.Response(200, json=page([], None)))
    config = load_ted_config("config/sources/ted.yaml")

    list(client.iter_notices("q", REQUESTED_FIELDS, page_size=config.page_size))

    assert respx.calls.call_count == 1


def test_the_offline_guard_is_never_looser_than_what_ted_measured() -> None:
    """55 fields were accepted at page size 181 and refused at 182, live.

    The local arithmetic is a precaution rather than a proven formula - one TED
    field name was found to cost more than one - so it has to sit on the safe side
    of the measurement, never past it.
    """
    assert max_page_size(55) <= 181
    assert fields_per_page(55, 175) <= MAX_FIELDS_PER_PAGE


def test_an_empty_field_list_is_refused_before_any_request(client: TedClient) -> None:
    with pytest.raises(QueryError, match="field list is empty"):
        list(client.iter_notices("q", []))


# ------------------------------------------------------------------ retries


@respx.mock
def test_a_429_is_retried_within_the_attempt_budget(client: TedClient) -> None:
    route = respx.post(TED_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(429, text=HTML_429, headers={"content-type": "text/html"}),
            httpx.Response(200, json=page([notice("1-2026")], None)),
        ]
    )

    assert [n["publication-number"] for n in client.iter_notices("q", FIELDS)] == ["1-2026"]
    assert route.call_count == 2


@respx.mock
def test_retries_are_bounded_and_then_the_failure_is_reported(client: TedClient) -> None:
    route = respx.post(TED_SEARCH_URL).mock(
        return_value=httpx.Response(429, text=HTML_429, headers={"content-type": "text/html"})
    )

    with pytest.raises(TransportError) as caught:
        list(client.iter_notices("q", FIELDS))

    assert route.call_count == 3
    assert caught.value.status == 429
    assert caught.value.attempts == 3


@respx.mock
def test_an_html_error_body_is_never_parsed_as_json(client: TedClient) -> None:
    respx.post(TED_SEARCH_URL).mock(
        return_value=httpx.Response(429, text=HTML_429, headers={"content-type": "text/html"})
    )

    with pytest.raises(TransportError):
        list(client.iter_notices("q", FIELDS))


@respx.mock
def test_retry_after_is_honoured_when_a_proxy_sends_one() -> None:
    waits: list[float] = []
    client = TedClient(max_attempts=2, page_pause=0.0, sleep=waits.append, backoff_max=120.0)

    respx.post(TED_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(
                429, text=HTML_429, headers={"content-type": "text/html", "retry-after": "7"}
            ),
            httpx.Response(200, json=page([], None)),
        ]
    )

    list(client.iter_notices("q", FIELDS))

    assert 7.0 in waits


@respx.mock
def test_a_rate_limit_without_retry_after_backs_off_about_thirty_seconds() -> None:
    waits: list[float] = []
    client = TedClient(max_attempts=2, page_pause=0.0, sleep=waits.append, rate_limit_backoff=30.0)

    respx.post(TED_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(429, text=HTML_429, headers={"content-type": "text/html"}),
            httpx.Response(200, json=page([], None)),
        ]
    )

    list(client.iter_notices("q", FIELDS))

    assert waits and 30.0 <= waits[0] <= 40.0


@respx.mock
def test_backoff_grows_and_stays_below_the_ceiling() -> None:
    waits: list[float] = []
    client = TedClient(
        max_attempts=4,
        page_pause=0.0,
        sleep=waits.append,
        backoff_initial=1.0,
        backoff_max=8.0,
    )

    respx.post(TED_SEARCH_URL).mock(return_value=httpx.Response(503))

    with pytest.raises(TransportError):
        list(client.iter_notices("q", FIELDS))

    assert len(waits) == 3
    assert waits[0] < waits[-1]
    assert all(wait <= 8.0 * 1.25 for wait in waits)


@respx.mock
def test_a_timeout_is_retried(client: TedClient) -> None:
    route = respx.post(TED_SEARCH_URL).mock(
        side_effect=[
            httpx.ReadTimeout("read timed out"),
            httpx.Response(200, json=page([notice("1-2026")], None)),
        ]
    )

    assert len(list(client.iter_notices("q", FIELDS))) == 1
    assert route.call_count == 2


@respx.mock
@pytest.mark.parametrize("status", [408, 500, 502, 503, 504])
def test_transient_statuses_are_retried(client: TedClient, status: int) -> None:
    route = respx.post(TED_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(status),
            httpx.Response(200, json=page([notice("1-2026")], None)),
        ]
    )

    assert len(list(client.iter_notices("q", FIELDS))) == 1
    assert route.call_count == 2


@respx.mock
def test_a_bad_request_is_never_retried(client: TedClient) -> None:
    route = respx.post(TED_SEARCH_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "message": "Syntax error in expert query at line 1, col 19",
                "error": {"type": "QUERY_SYNTAX_ERROR", "fieldName": "classification-cpv"},
            },
        )
    )

    with pytest.raises(QueryError) as caught:
        list(client.iter_notices("bad query", FIELDS))

    assert route.call_count == 1
    assert caught.value.field == "classification-cpv"
    assert "Syntax error" in str(caught.value)


# ------------------------------------------------------------- timed-out pages


@respx.mock
def test_a_timed_out_page_is_retried_and_then_raised(client: TedClient) -> None:
    envelope = {
        "notices": [notice("1-2026")],
        "totalNoticeCount": 9999,
        "iterationNextToken": "TOKEN-A",
        "timedOut": True,
    }
    route = respx.post(TED_SEARCH_URL).mock(return_value=httpx.Response(200, json=envelope))

    with pytest.raises(InvalidPayloadError, match="watermark must not advance"):
        list(client.iter_notices("q", FIELDS))

    assert route.call_count == 3


@respx.mock
def test_a_timed_out_page_that_succeeds_on_retry_is_used(client: TedClient) -> None:
    timed_out = {"notices": [], "iterationNextToken": None, "timedOut": True}
    route = respx.post(TED_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=timed_out),
            httpx.Response(200, json=page([notice("1-2026")], None)),
        ]
    )

    assert len(list(client.iter_notices("q", FIELDS))) == 1
    assert route.call_count == 2


# ------------------------------------------------------------- bad envelopes


@respx.mock
def test_a_non_json_success_body_is_rejected(client: TedClient) -> None:
    respx.post(TED_SEARCH_URL).mock(
        return_value=httpx.Response(
            200, text="<html>ok</html>", headers={"content-type": "text/html"}
        )
    )

    with pytest.raises(InvalidPayloadError, match="expected JSON"):
        list(client.iter_notices("q", FIELDS))


@respx.mock
def test_a_notices_value_that_is_not_a_list_is_rejected(client: TedClient) -> None:
    respx.post(TED_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"notices": "nope", "iterationNextToken": None})
    )

    with pytest.raises(InvalidPayloadError, match="to be a list"):
        list(client.iter_notices("q", FIELDS))


@respx.mock
def test_a_missing_notices_key_is_an_empty_page_not_a_crash(client: TedClient) -> None:
    respx.post(TED_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"totalNoticeCount": 0, "timedOut": False})
    )

    assert list(client.iter_notices("q", FIELDS)) == []


# --------------------------------------------------------------- validation


@respx.mock
def test_validate_query_uses_check_query_syntax(client: TedClient) -> None:
    respx.post(TED_SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json={"notices": [], "totalNoticeCount": None, "timedOut": False}
        )
    )

    client.validate_query("publication-date>=today(-1)", FIELDS)

    assert "checkQuerySyntax" in respx.calls[0].request.read().decode()


@respx.mock
def test_a_broken_query_fails_immediately_with_the_reason(client: TedClient) -> None:
    respx.post(TED_SEARCH_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "message": "Unknown search field 'not-a-field' found in expert query",
                "error": {"type": "QUERY_UNKNOWN_FIELD", "fieldName": "not-a-field"},
            },
        )
    )

    with pytest.raises(QueryError, match="not-a-field"):
        client.validate_query("not-a-field=1", FIELDS)


@respx.mock
def test_unknown_fields_are_named_from_teds_supported_list(client: TedClient) -> None:
    # TED lists everything it supports but never says which of ours was wrong.
    respx.post(TED_SEARCH_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "message": (
                    "Parameter 'fields' contains unsupported value "
                    "(supported values are: publication-number,notice-title,buyer-country)"
                ),
                "error": None,
            },
        )
    )

    unknown = client.unknown_fields(["publication-number", "place-of-performance", "made-up"])

    assert unknown == ["place-of-performance", "made-up"]


@respx.mock
def test_a_valid_field_list_reports_nothing_unknown(client: TedClient) -> None:
    respx.post(TED_SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json={"notices": [], "totalNoticeCount": None, "timedOut": False}
        )
    )

    assert client.unknown_fields(FIELDS) == []


@respx.mock
def test_a_400_that_is_not_about_fields_is_not_swallowed(client: TedClient) -> None:
    respx.post(TED_SEARCH_URL).mock(
        return_value=httpx.Response(
            400, json={"message": "Syntax error in expert query", "error": None}
        )
    )

    with pytest.raises(QueryError):
        client.unknown_fields(FIELDS)


# ------------------------------------------------------------------- pacing


@respx.mock
def test_pages_are_fetched_one_at_a_time_with_a_pause_between_them() -> None:
    waits: list[float] = []
    client = TedClient(max_attempts=2, page_pause=1.5, sleep=waits.append)

    respx.post(TED_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=page([notice("1-2026")], "TOKEN-A")),
            httpx.Response(200, json=page([notice("2-2026")], None)),
        ]
    )

    list(client.iter_notices("q", FIELDS))

    assert waits == [1.5]
