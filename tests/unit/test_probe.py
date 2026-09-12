"""What `watchdog ted-probe` promises: N days means N, and the limit is never silent.

Both properties exist because a wrong conclusion was drawn from a probe: the first
60 rows of a 212-notice window read as the whole window, and the half of the week
that contained the relevant notices was never printed.

No test here touches the network; the three requests a probe makes - count,
validate, search - are answered from recorded shapes.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx
from tests.conftest import load_ted_fixture

from watchdog.services.ted import probe, window_for
from watchdog.sources.ted import TedClient, TedSourceConfig
from watchdog.sources.ted.client import TED_SEARCH_URL

TODAY = date(2026, 9, 12)


@pytest.mark.parametrize(
    ("days", "expected_from"),
    [
        (1, date(2026, 9, 12)),
        (2, date(2026, 9, 11)),
        (7, date(2026, 9, 6)),
        (30, date(2026, 8, 14)),
    ],
)
def test_days_means_that_many_calendar_days_counting_today(days: int, expected_from: date) -> None:
    # The window is inclusive at both ends, so days=7 must span seven dates, not eight.
    window_from, window_to = window_for(days, today=TODAY)

    assert (window_from, window_to) == (expected_from, TODAY)
    assert (window_to - window_from).days + 1 == days


@respx.mock
def test_the_probe_says_how_many_notices_the_limit_hid() -> None:
    notice = load_ted_fixture("form_type_competition_is_a_contract_notice")
    respx.post(TED_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json={"notices": [], "totalNoticeCount": 212, "timedOut": False}),
            httpx.Response(200, json={"notices": [], "totalNoticeCount": None, "timedOut": False}),
            httpx.Response(
                200,
                json={
                    "notices": [notice, notice, notice],
                    "totalNoticeCount": 212,
                    "iterationNextToken": None,
                    "timedOut": False,
                },
            ),
        ]
    )

    with TedClient(page_pause=0.0, sleep=lambda _seconds: None) as client:
        result = probe(TedSourceConfig(), days=7, limit=2, client=client)

    assert len(result.rows) == 2
    assert result.total == 212
    assert result.truncated is True


@respx.mock
def test_a_window_smaller_than_the_limit_is_not_reported_as_truncated() -> None:
    notice = load_ted_fixture("form_type_competition_is_a_contract_notice")
    respx.post(TED_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json={"notices": [], "totalNoticeCount": 1, "timedOut": False}),
            httpx.Response(200, json={"notices": [], "totalNoticeCount": None, "timedOut": False}),
            httpx.Response(
                200,
                json={
                    "notices": [notice],
                    "totalNoticeCount": 1,
                    "iterationNextToken": None,
                    "timedOut": False,
                },
            ),
        ]
    )

    with TedClient(page_pause=0.0, sleep=lambda _seconds: None) as client:
        result = probe(TedSourceConfig(), days=7, limit=25, client=client)

    assert result.total == 1
    assert result.truncated is False
