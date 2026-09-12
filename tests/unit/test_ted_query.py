"""Building the TED expert query, and refusing to accept configuration TED will not take."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from watchdog.core.cpv import cpv_matches
from watchdog.core.enums import ContractNature, NoticeStage
from watchdog.sources.ted.config import load_ted_config, parse_ted_config
from watchdog.sources.ted.query import QueryProfile, build_query

WINDOW_FROM = date(2026, 9, 5)
WINDOW_TO = date(2026, 9, 12)


def config(**overrides: object):
    base: dict[str, object] = {
        "cpv_prefixes": ["71241000", "09300000"],
        "buyer_countries": ["NOR", "DEU"],
        "place_of_performance": [],
        "contract_natures": ["services"],
        "notice_stages": ["prior_information", "market_consultation", "contract_notice"],
        "extra_query": None,
        "overlap_days": 2,
    }
    base.update(overrides)
    return parse_ted_config(base)


# ------------------------------------------------------------------- the query


def test_the_query_filters_on_cpv_stages_nature_country_and_the_window() -> None:
    query = build_query(config(), WINDOW_FROM, WINDOW_TO)

    assert "classification-cpv IN (71241000 09300000)" in query
    assert "form-type IN (planning consultation competition)" in query
    assert "contract-nature IN (services)" in query
    assert "buyer-country IN (NOR DEU)" in query
    assert "publication-date>=20260905 AND publication-date<=20260912" in query


def test_dates_use_teds_own_format_and_not_iso() -> None:
    # TED rejects 2026-09-05 with QUERY_INVALID_FIELD_FORMAT.
    query = build_query(config(), WINDOW_FROM, WINDOW_TO)

    assert "2026-09-05" not in query
    assert "20260905" in query


def test_a_leading_zero_cpv_code_reaches_the_query_intact() -> None:
    query = build_query(config(cpv_prefixes=["09310000"]), WINDOW_FROM, WINDOW_TO)

    assert "09310000" in query
    assert "9310000)" not in query.replace("09310000", "")


def test_no_entr_keyword_ever_reaches_ted() -> None:
    # Source-language differences would destroy recall; keywords stay local.
    query = build_query(config(), WINDOW_FROM, WINDOW_TO).lower()

    for keyword in ("hydrogen", "feasibility", "advisory", "carbon", "offshore"):
        assert keyword not in query


def test_the_window_is_always_present() -> None:
    query = build_query(
        config(cpv_prefixes=[], buyer_countries=[], notice_stages=[], contract_natures=[]),
        WINDOW_FROM,
        WINDOW_TO,
    )

    assert query == "(publication-date>=20260905 AND publication-date<=20260912)"


def test_a_backwards_window_is_refused() -> None:
    with pytest.raises(ValueError, match="before"):
        build_query(config(), WINDOW_TO, WINDOW_FROM)


def test_place_of_performance_is_a_separate_filter_from_the_buyer() -> None:
    query = build_query(config(place_of_performance=["MAR"]), WINDOW_FROM, WINDOW_TO)

    assert "buyer-country IN (NOR DEU)" in query
    assert "place-of-performance-country-proc IN (MAR)" in query


def test_extra_query_is_appended() -> None:
    query = build_query(config(extra_query="notice-type!=qu-sy"), WINDOW_FROM, WINDOW_TO)

    assert query.endswith("AND (notice-type!=qu-sy)")


def test_stages_map_to_form_types_without_duplicates() -> None:
    query = build_query(
        config(notice_stages=["contract_notice", "contract_notice"]), WINDOW_FROM, WINDOW_TO
    )

    assert "form-type IN (competition)" in query


# ------------------------------------------------------------------ the audit


def test_the_audit_profile_drops_both_the_cpv_and_the_nature_filter() -> None:
    # The monthly recall check has to be able to see what the daily query misses,
    # and the services filter is one of the things being checked.
    query = build_query(config(), WINDOW_FROM, WINDOW_TO, profile=QueryProfile.AUDIT)

    assert "classification-cpv" not in query
    assert "contract-nature" not in query
    assert "form-type IN (planning consultation competition)" in query
    assert "publication-date>=20260905" in query


# ------------------------------------------------------------- configuration


def test_an_unquoted_leading_zero_code_is_padded_back() -> None:
    # 09310000 in YAML without quotes arrives here as the integer 9310000.
    assert parse_ted_config({"cpv_prefixes": [9310000]}).cpv_prefixes == ["09310000"]


def test_a_nonsense_cpv_code_is_refused_with_a_readable_message() -> None:
    with pytest.raises(ValidationError, match="not a CPV code"):
        parse_ted_config({"cpv_prefixes": ["not-a-code"]})


def test_a_two_letter_country_is_refused() -> None:
    with pytest.raises(ValidationError, match="alpha-3"):
        parse_ted_config({"buyer_countries": ["NO"]})


def test_a_country_we_cannot_name_is_refused() -> None:
    # Otherwise the register would show a bare code to a non-technical reader.
    with pytest.raises(ValidationError, match="cannot name|does not"):
        parse_ted_config({"buyer_countries": ["ZZZ"]})


def test_a_page_size_above_teds_maximum_is_refused() -> None:
    with pytest.raises(ValidationError):
        parse_ted_config({"page_size": 251})


def test_overlap_is_expressed_in_days_because_ted_has_no_hours() -> None:
    parsed = parse_ted_config({"overlap_days": 2})

    assert parsed.overlap_days == 2
    assert not hasattr(parsed, "overlap_hours")


# ------------------------------------------------------- the shipped config


def test_the_shipped_configuration_loads_and_builds_a_query() -> None:
    shipped = load_ted_config("config/sources/ted.yaml")

    assert "71241000" in shipped.cpv_prefixes
    assert "09300000" in shipped.cpv_prefixes
    # Added after measuring 5-12 September: 45251000 was the only code that
    # reached the Eidsiva CCS-as-a-service RFI, and 76000000 was our only gap in
    # subsurface and gas-infrastructure services. See docs/decisions/0004.
    assert "45251000" in shipped.cpv_prefixes
    assert "76000000" in shipped.cpv_prefixes
    assert shipped.contract_natures == [ContractNature.SERVICES]
    assert NoticeStage.MARKET_CONSULTATION in shipped.notice_stages
    assert NoticeStage.PRIOR_INFORMATION in shipped.notice_stages
    assert shipped.overlap_days == 2
    assert shipped.page_size == 250
    assert "NOR" in shipped.buyer_countries
    assert shipped.place_of_performance == []

    query = build_query(shipped, WINDOW_FROM, WINDOW_TO)
    assert query.startswith("(classification-cpv IN (")


def test_no_shipped_cpv_code_is_redundant_under_another() -> None:
    # TED's filter is hierarchical, so a code whose parent is also configured
    # can never return anything of its own. Three such entries were removed on
    # 2026-09-12; this stops the next one being added unnoticed.
    shipped = load_ted_config("config/sources/ted.yaml")

    redundant = [
        (child, parent)
        for parent in shipped.cpv_prefixes
        for child in shipped.cpv_prefixes
        if child != parent and cpv_matches(parent, child)
    ]

    assert redundant == []


# ------------------------------------------------------- provisional codes


def test_a_provisional_code_is_searched_for_like_any_other() -> None:
    parsed = parse_ted_config(
        {
            "cpv_prefixes": [
                "71241000",
                {
                    "code": "09123000",
                    "provisional": {"since": "2026-09-12", "reason": "a bet on hydrogen blending"},
                },
            ]
        }
    )

    assert parsed.cpv_prefixes == ["71241000", "09123000"]
    assert "09123000" in build_query(parsed, WINDOW_FROM, WINDOW_TO)


def test_a_provisional_code_keeps_its_reason_and_the_date_it_went_on_trial() -> None:
    parsed = parse_ted_config(
        {
            "cpv_prefixes": [
                {
                    "code": "09123000",
                    "provisional": {"since": "2026-09-12", "reason": "a bet on hydrogen blending"},
                }
            ]
        }
    )

    assert len(parsed.provisional_cpv) == 1
    assert parsed.provisional_cpv[0].code == "09123000"
    assert parsed.provisional_cpv[0].since == date(2026, 9, 12)
    assert parsed.provisional_cpv[0].reason == "a bet on hydrogen blending"


def test_a_provisional_block_without_a_reason_is_refused() -> None:
    # A bet nobody wrote down cannot be settled, so it is not accepted.
    with pytest.raises(ValidationError):
        parse_ted_config(
            {"cpv_prefixes": [{"code": "09123000", "provisional": {"since": "2026-09-12"}}]}
        )


def test_a_cpv_entry_without_a_code_is_refused() -> None:
    with pytest.raises(ValidationError):
        parse_ted_config(
            {"cpv_prefixes": [{"provisional": {"reason": "x", "since": "2026-09-12"}}]}
        )


def test_the_shipped_provisional_codes_are_the_two_that_were_measured_as_bets() -> None:
    shipped = load_ted_config("config/sources/ted.yaml")

    provisional = {entry.code for entry in shipped.provisional_cpv}

    assert provisional == {"71334000", "09123000"}
    # Provisional is a label for the audit, not a different kind of search.
    assert provisional <= set(shipped.cpv_prefixes)
    assert all(entry.reason.strip() for entry in shipped.provisional_cpv)
