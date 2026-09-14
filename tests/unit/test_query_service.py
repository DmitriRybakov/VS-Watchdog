"""The query service: combined filters, invalid values, sorting, paging.

These are the tests that stop the register showing a different set of notices
from the one a colleague asked for. Each one fixes a rule that is easy to get
wrong and impossible to see on screen when it is.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from watchdog.core.enums import (
    Band,
    ContractNature,
    DeadlineType,
    Domain,
    NoticeStage,
    RuleSignal,
    RulesRoute,
    RuleStrength,
    SourcePlatform,
    Verdict,
)
from watchdog.core.models import (
    Review,
    RuleMatch,
    RulesResult,
    ScreeningResult,
    Tender,
    TextBlock,
)
from watchdog.services import query as query_service
from watchdog.services.query import ALL_BANDS, FilterError
from watchdog.storage.repository import MAX_PAGE_SIZE, Repository

TODAY = date(2026, 9, 13)
RUN = "run-under-test"


# --------------------------------------------------------------------- fixtures


def _tender(
    source_id: str,
    *,
    title: str = "Denmark - Engineering services - Forundersogelse",
    native: str = "Forundersogelse af brintanlaeg",
    buyer_country: str = "DNK",
    performance: list[str] | None = None,
    published: date = date(2026, 9, 1),
    deadline: date | None = date(2026, 9, 30),
    deadline_type: DeadlineType = DeadlineType.TENDER_SUBMISSION,
) -> Tender:
    return Tender(
        source=SourcePlatform.TED,
        source_id=source_id,
        title=title,
        title_language="eng",
        title_native=native,
        title_native_language="dan",
        description="A feasibility study for a hydrogen facility.",
        buyer_name="Energinet",
        buyer_country=buyer_country,
        place_of_performance_country=performance if performance is not None else [buyer_country],
        published_date=published,
        deadline_date=deadline,
        deadline=(
            datetime(deadline.year, deadline.month, deadline.day, 12, tzinfo=UTC)
            if deadline is not None
            else None
        ),
        deadline_type=deadline_type,
        notice_stage=NoticeStage.CONTRACT_NOTICE,
        contract_nature=ContractNature.SERVICES,
        contract_natures=[ContractNature.SERVICES],
        cpv_all=["71241000"],
        screening_blocks=[TextBlock(field="title-proc", language="dan", text=native)],
    )


def _result(
    tender: Tender,
    *,
    score: int = 2,
    band: Band = Band.REVIEW,
    rules_only: int | None = 2,
    domains: list[Domain] | None = None,
    rule_ids: tuple[str, ...] = ("hydrogen",),
    reason_codes: tuple[str, ...] = ("RULES_ONLY",),
    confidence: float = 0.6,
) -> ScreeningResult:
    matches = [
        RuleMatch(
            rule_id=rule_id,
            signal=RuleSignal.DOMAIN,
            strength=RuleStrength.HIGH,
            alias_matched="brint",
            field="title-proc",
            evidence="Forundersogelse af brintanlaeg",
        )
        for rule_id in rule_ids
    ]
    return ScreeningResult(
        tender_id=tender.id,
        score=score,
        band=band,
        rules_only_score=rules_only,
        confidence=confidence,
        reason_codes=list(reason_codes),
        domain_strength_rank=3 if domains else 0,
        domain_rules_matched=len(matches),
        rules=RulesResult(
            tender_id=tender.id,
            matches=matches,
            domains_hit=domains or [Domain.HYDROGEN],
            route=RulesRoute.ASSESS,
            rules_version="3",
        ),
        explanation="Rules-only grade 2.",
        screened_content_hash=tender.content_hash,
        provider="disabled",
        rules_version="3",
        policy_version="1",
        profile_version="1",
    )


@pytest.fixture
def register(repository: Repository) -> Repository:
    """A small register with one notice of every shape the filters care about."""
    open_hydrogen = _tender("1-2026")
    closed_hydrogen = _tender("2-2026", deadline=date(2026, 8, 1))
    no_deadline = _tender("3-2026", deadline=None)
    archived = _tender("4-2026", native="Skolebyggeri", buyer_country="SWE")
    foreign_work = _tender("5-2026", buyer_country="FRA", performance=["AGO"])
    unscreened = _tender("6-2026", buyer_country="NOR")

    repository.upsert_tenders(
        [open_hydrogen, closed_hydrogen, no_deadline, archived, foreign_work, unscreened],
        run_id=RUN,
    )

    repository.save_screening_result(_result(open_hydrogen, rules_only=3, score=3))
    repository.save_screening_result(_result(closed_hydrogen))
    repository.save_screening_result(_result(no_deadline))
    repository.save_screening_result(
        _result(
            archived,
            score=1,
            band=Band.ARCHIVE,
            rules_only=0,
            domains=[],
            rule_ids=(),
            reason_codes=("RULES_ONLY_NO_EVIDENCE",),
        )
    )
    repository.save_screening_result(
        _result(foreign_work, rules_only=1, domains=[Domain.GRID_TRANSMISSION], rule_ids=("grid",))
    )
    return repository


def _view(register: Repository, params: dict[str, object], *, ai: bool = False):
    parsed = query_service.parse(params, ai_enabled=ai, as_of=TODAY)
    return query_service.build(parsed, repository=register, ai_enabled=ai, as_of=TODAY)


def _ids(view) -> list[str]:
    return [row.tender.source_id for row in view.rows]


# ------------------------------------------------------------------- defaults


def test_the_default_follows_the_provider_state(register: Repository) -> None:
    """Review only with no model; shortlist and review once one is configured."""
    off = query_service.parse({}, ai_enabled=False, as_of=TODAY)
    on = query_service.parse({}, ai_enabled=True, as_of=TODAY)

    assert off.filters.bands == [Band.REVIEW]
    assert on.filters.bands == [Band.SHORTLIST, Band.REVIEW]


def test_the_default_hides_past_deadlines_but_keeps_notices_with_none(
    register: Repository,
) -> None:
    """A missing deadline is unknown, not past. Hiding it would lose the notice."""
    view = _view(register, {})

    assert "2-2026" not in _ids(view), "a notice that closed in August is hidden"
    assert "3-2026" in _ids(view), "a notice with no deadline at all is kept"


def test_including_past_deadlines_brings_the_closed_one_back(register: Repository) -> None:
    view = _view(register, {"past": "1"})

    assert "2-2026" in _ids(view)


def test_the_default_order_puts_the_strongest_evidence_first(register: Repository) -> None:
    view = _view(register, {"past": "1"})

    grades = [row.evidence_grade for row in view.rows]
    assert grades == sorted(grades, reverse=True)
    assert view.rows[0].tender.source_id == "1-2026"


# -------------------------------------------------------------------- filters


def test_filters_combine_rather_than_replace_one_another(register: Repository) -> None:
    view = _view(register, {"band": ALL_BANDS, "past": "1", "domain": "hydrogen", "q": "brint"})

    assert sorted(_ids(view)) == ["1-2026", "2-2026", "3-2026"]


def test_buyer_country_and_country_of_performance_are_different_questions(
    register: Repository,
) -> None:
    """A French buyer procuring a study for Angola is found by either, not by one."""
    by_buyer = _view(register, {"band": ALL_BANDS, "past": "1", "buyer_country": "FRA"})
    by_work = _view(register, {"band": ALL_BANDS, "past": "1", "performance_country": "AGO"})

    assert _ids(by_buyer) == ["5-2026"]
    assert _ids(by_work) == ["5-2026"]
    assert _ids(_view(register, {"band": ALL_BANDS, "past": "1", "buyer_country": "AGO"})) == []


def test_domain_and_rule_filters_read_the_mirrored_columns(register: Repository) -> None:
    view = _view(register, {"band": ALL_BANDS, "past": "1", "rule": "grid"})

    assert _ids(view) == ["5-2026"]


def test_reason_tag_filter_finds_the_no_evidence_group(register: Repository) -> None:
    view = _view(register, {"band": ALL_BANDS, "past": "1", "tag": "rules_only_no_evidence"})

    assert _ids(view) == ["4-2026"]


def test_unscreened_notices_are_findable_and_are_not_archived(register: Repository) -> None:
    view = _view(register, {"band": ALL_BANDS, "past": "1", "unscreened": "1"})

    assert _ids(view) == ["6-2026"]


def test_undecided_only_drops_a_notice_once_a_verdict_exists(register: Repository) -> None:
    before = _view(register, {"band": ALL_BANDS, "past": "1", "undecided": "1"})
    assert "1-2026" in _ids(before)

    register.save_review(
        Review(tender_id="ted:1-2026", verdict=Verdict.RELEVANT, reviewed_by="tester")
    )

    after = _view(register, {"band": ALL_BANDS, "past": "1", "undecided": "1"})
    assert "1-2026" not in _ids(after)
    assert _ids(_view(register, {"band": ALL_BANDS, "past": "1", "verdict": "relevant"})) == [
        "1-2026"
    ]


def test_closing_within_is_bounded_at_both_ends(register: Repository) -> None:
    """ "Closing soon" must never mean "closed weeks ago"."""
    view = _view(register, {"band": ALL_BANDS, "past": "1", "closing": 30})

    assert "2-2026" not in _ids(view), "closed on 1 August, not closing soon"
    assert "1-2026" in _ids(view)


def test_all_bands_is_a_value_not_an_absent_parameter(register: Repository) -> None:
    default = _view(register, {})
    everything = _view(register, {"band": ALL_BANDS, "past": "1"})

    assert Band.ARCHIVE.value not in [row.screening.band.value for row in default.rows]
    assert "4-2026" in _ids(everything)


def test_a_percent_sign_in_the_search_box_is_a_percent_sign(register: Repository) -> None:
    view = _view(register, {"band": ALL_BANDS, "past": "1", "q": "%"})

    assert _ids(view) == []


# --------------------------------------------------------------- invalid input


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"band": "urgent"}, "not a valid band"),
        ({"domain": "nuclear"}, "not a valid domain"),
        ({"verdict": "maybe"}, "not a valid verdict"),
        ({"source": "doffin_v2"}, "not a valid source"),
        ({"sort": "buyer_name; drop table tender"}, "not a column this register can sort by"),
        ({"sort": "raw"}, "not a column this register can sort by"),
        ({"dir": "sideways"}, "not a sort direction"),
        ({"mode": "kanban"}, "not a view mode"),
        ({"score_min": "high"}, "must be a whole number"),
        ({"score_min": "9"}, "must be between 1 and 5"),
        ({"published_from": "last tuesday"}, "must be a date written as YYYY-MM-DD"),
    ],
)
def test_an_unreadable_filter_is_refused_by_name(params: dict[str, str], expected: str) -> None:
    """Refused, never silently dropped: a dropped filter shows the wrong notices."""
    with pytest.raises(FilterError) as raised:
        query_service.parse(params, ai_enabled=False, as_of=TODAY)

    assert expected in str(raised.value)


def test_an_unknown_sort_key_never_reaches_sql(repository: Repository) -> None:
    with pytest.raises(ValueError, match="unknown sort key"):
        repository.list_register(sort_by="; drop table tender")


def test_every_offered_sort_key_actually_works(register: Repository) -> None:
    for key in query_service.SORTS:
        view = _view(register, {"band": ALL_BANDS, "past": "1", "sort": key})
        assert view.page.total == 6, f"sort key {key} changed which rows matched"


# ------------------------------------------------------------ paging and bounds


def test_page_size_is_bounded_at_both_ends(register: Repository) -> None:
    with pytest.raises(FilterError, match="must be between"):
        query_service.parse({"limit": "5000"}, ai_enabled=False, as_of=TODAY)

    assert query_service.parse({"limit": "200"}, ai_enabled=False, as_of=TODAY).limit == 200


def test_the_repository_caps_a_limit_rather_than_refusing_it(register: Repository) -> None:
    page = register.list_register(limit=100_000)

    assert page.limit == MAX_PAGE_SIZE


def test_paging_never_shows_the_same_notice_twice(register: Repository) -> None:
    seen: list[str] = []
    for offset in (0, 5):
        view = _view(register, {"band": ALL_BANDS, "past": "1", "limit": 5, "offset": offset})
        seen.extend(_ids(view))

    assert len(seen) == len(set(seen)) == 6


def test_an_offset_past_the_end_is_an_empty_page_not_an_error(register: Repository) -> None:
    view = _view(register, {"band": ALL_BANDS, "past": "1", "offset": 500})

    assert view.rows == []
    assert view.page.total == 6


def test_empty_results_still_report_the_surrounding_state(repository: Repository) -> None:
    view = _view(repository, {})

    assert view.rows == []
    assert view.page.total == 0
    assert view.empty_database is True


# ----------------------------------------------- the headline and saved views


def test_every_headline_figure_matches_the_view_its_link_opens(register: Repository) -> None:
    """The number and the page behind it are one query, so they cannot disagree."""
    view = _view(register, {})
    head = view.headline
    assert head is not None

    for count in [*head.figures, head.archived]:
        opened = _view(register, _params(count.query_string))
        assert opened.page.total == count.count, f"{count.key} disagrees with its own link"


def test_every_headline_figure_says_which_population_it_counted(register: Repository) -> None:
    """Three numbers side by side invite arithmetic. Each has to say what it is of."""
    head = _view(register, {}).headline
    assert head is not None

    for count in head.figures:
        assert count.note, f"{count.key} does not say what it was counted out of"


def test_the_headline_states_everything_held_and_what_was_archived(
    register: Repository,
) -> None:
    """ "Worth reading: 23" means nothing without the pile it was drawn from."""
    view = _view(register, {})
    head = view.headline
    assert head is not None

    everything = _view(register, _params(head.everything_query))

    assert head.total_held == everything.page.total
    assert head.total_held > 0


def test_the_new_figure_falls_back_to_a_window_when_nothing_has_been_fetched(
    repository: Repository,
) -> None:
    """ "Since the last update" has no meaning before there has been one, and says so."""
    repository.upsert_tenders(
        [_tender("7-2026", published=date(2026, 9, 12), native="Skolegard")], run_id=RUN
    )
    head = _view(repository, {}).headline
    assert head is not None

    assert head.new.key == "new_this_week"
    assert head.new.count == 1


def test_saved_views_do_not_offer_a_band_no_model_can_reach(register: Repository) -> None:
    off = {view.key for view in query_service.saved_views(ai_enabled=False)}
    on = {view.key for view in query_service.saved_views(ai_enabled=True)}

    assert "shortlist" not in off and "review" not in off
    assert "evidence" in off
    assert "shortlist" in on and "review" in on and "evidence" not in on


def test_the_query_string_round_trips(register: Repository) -> None:
    original = query_service.parse(
        {"band": ["review"], "domain": "hydrogen", "q": "brint", "limit": "10", "offset": "10"},
        ai_enabled=False,
        as_of=TODAY,
    )
    again = query_service.parse(_params(original.query_string()), ai_enabled=False, as_of=TODAY)

    assert again.filters == original.filters
    assert (again.sort, again.limit, again.offset) == (
        original.sort,
        original.limit,
        original.offset,
    )


def test_days_left_is_none_rather_than_zero_when_no_deadline_is_known(
    register: Repository,
) -> None:
    view = _view(register, {"band": ALL_BANDS, "past": "1"})
    row = next(row for row in view.rows if row.tender.source_id == "3-2026")

    assert row.days_left is None


def _params(query_string: str) -> dict[str, list[str]]:
    from urllib.parse import parse_qs

    return parse_qs(query_string)
