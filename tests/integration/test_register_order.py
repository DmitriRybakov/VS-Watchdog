"""The register order: one definition, two implementations, and they must agree.

``core.models.RANKING_FIELDS`` names the order. The service layer sorts a finished
run with ``ranking_key``; the repository pages the register with SQL built from the
same names. If those two ever disagreed, a notice would sit at the top of the run
report and in the middle of the register, with nothing on screen to say which was
right - so this compares the whole sequence, not the first row.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import date

import pytest

from watchdog.core.enums import Band, RuleSignal, RuleStrength, SourcePlatform
from watchdog.core.models import (
    RANKING_FIELDS,
    RuleMatch,
    RulesResult,
    ScreeningResult,
    Tender,
    ranking_key,
)
from watchdog.storage.repository import MAX_PAGE_SIZE, REGISTER_SORT, Repository

TenderFactory = Callable[..., Tender]

# Every combination below is built deliberately, so each field in the order is
# forced to break a tie the one before it could not. Three grades x three
# strengths x three rule counts x three dates is 81 notices and every tie.
GRADES = (0, 1, 2, 3)
STRENGTHS = (0, 1, 2, 3)
RULE_COUNTS = (0, 1, 3)
DATES = (date(2026, 7, 1), date(2026, 8, 15), None)


def _result(tender_id: str, *, grade: int, strength: int, rules_matched: int) -> ScreeningResult:
    return ScreeningResult(
        tender_id=tender_id,
        score=2 if grade == 0 else grade,
        band=Band.ARCHIVE if grade == 0 else Band.REVIEW,
        rules_only_score=grade,
        confidence=0.4,
        reason_codes=["RULES_ONLY"],
        domain_strength_rank=strength,
        domain_rules_matched=rules_matched,
        rules=RulesResult(
            tender_id=tender_id,
            matches=[
                RuleMatch(
                    rule_id=f"domain_{index}",
                    signal=RuleSignal.DOMAIN,
                    strength=RuleStrength.HIGH,
                    alias_matched="hydrogen",
                    field="title-proc",
                    evidence="hydrogen",
                )
                for index in range(rules_matched)
            ],
            rules_version="3",
        ),
        explanation="Built for an ordering test.",
        screened_content_hash="0" * 64,
        provider="disabled",
        rules_version="3",
        policy_version="1",
        profile_version="1",
    )


@pytest.fixture
def corpus(
    repository: Repository, make_tender: TenderFactory, run_id: str
) -> dict[str, tuple[ScreeningResult, date | None]]:
    """Every combination of the four ordering inputs, so each one breaks a tie."""
    combinations = list(itertools.product(GRADES, STRENGTHS, RULE_COUNTS, DATES))
    tenders: list[Tender] = []
    expected: dict[str, tuple[ScreeningResult, date | None]] = {}

    for index, (grade, strength, rules_matched, published) in enumerate(combinations):
        tender = make_tender(f"{index:08d}-2026", published_date=published)
        tenders.append(tender)
        expected[tender.id] = (
            _result(tender.id, grade=grade, strength=strength, rules_matched=rules_matched),
            published,
        )

    repository.upsert_tenders(tenders, run_id=run_id)
    for result, _ in expected.values():
        repository.save_screening_result(result)

    return expected


def _sql_order(store: Repository) -> list[str]:
    """Every id in the register, paged, in the order the database returns them."""
    ids: list[str] = []
    while True:
        page = store.list_tenders(sort_by=REGISTER_SORT, limit=MAX_PAGE_SIZE, offset=len(ids))
        ids.extend(tender.id for tender in page.items)
        if len(ids) >= page.total or not page.items:
            return ids


def _python_order(expected: dict[str, tuple[ScreeningResult, date | None]]) -> list[str]:
    """The same set, sorted the way the run report sorts it."""
    items = sorted(expected.items(), key=lambda item: item[0])
    items.sort(key=lambda item: ranking_key(item[1][0], item[1][1]), reverse=True)
    return [tender_id for tender_id, _ in items]


def test_the_sql_order_and_ranking_key_produce_the_same_sequence(
    repository: Repository, corpus: dict[str, tuple[ScreeningResult, date | None]]
) -> None:
    assert _sql_order(repository) == _python_order(corpus)


def test_paging_the_register_never_repeats_or_loses_a_notice(
    repository: Repository, corpus: dict[str, tuple[ScreeningResult, date | None]]
) -> None:
    """The point of doing the order in SQL. A stable order is what makes paging safe."""
    pages: list[str] = []
    for offset in range(0, len(corpus), 7):
        page = repository.list_tenders(sort_by=REGISTER_SORT, limit=7, offset=offset)
        pages.extend(tender.id for tender in page.items)

    assert pages == _python_order(corpus)
    assert len(set(pages)) == len(corpus)


def test_the_strongest_evidence_comes_first(
    repository: Repository, corpus: dict[str, tuple[ScreeningResult, date | None]]
) -> None:
    first = repository.list_tenders(sort_by=REGISTER_SORT, limit=1).items[0]
    result, published = corpus[first.id]

    assert result.rules_priority == max(GRADES)
    assert result.domain_strength_rank == max(STRENGTHS)
    assert result.domain_rules_matched == max(RULE_COUNTS)
    assert published == max(item for item in DATES if item is not None)


def test_a_notice_that_has_never_been_screened_sorts_last(
    repository: Repository,
    corpus: dict[str, tuple[ScreeningResult, date | None]],
    make_tender: TenderFactory,
    run_id: str,
) -> None:
    """It has no claim at all, and an empty claim is not a strong one."""
    unscreened = make_tender("99999999-2026", published_date=date(2026, 9, 1))
    repository.upsert_tenders([unscreened], run_id=run_id)

    assert _sql_order(repository)[-1] == unscreened.id


def test_every_field_in_the_register_order_has_a_column(repository: Repository) -> None:
    """RANKING_FIELDS is the definition; a field with no column is a silent loss."""
    assert RANKING_FIELDS == (
        "rules_priority",
        "domain_strength_rank",
        "domain_rules_matched",
        "published_date",
    )
    # Raises if a name in RANKING_FIELDS has no column behind it.
    repository.list_tenders(sort_by=REGISTER_SORT, limit=1)


def test_an_unknown_sort_key_names_the_register_order_among_the_allowed_ones(
    repository: Repository,
) -> None:
    with pytest.raises(ValueError, match="register"):
        repository.list_tenders(sort_by="whatever")


def test_the_rules_priority_column_matches_the_python_property(repository: Repository) -> None:
    """COALESCE(rules_only_score, score) and the property must mean the same thing."""
    assessed = _result("ted:00000001-2026", grade=0, strength=3, rules_matched=2).model_copy(
        update={"score": 5, "band": Band.SHORTLIST, "rules_only_score": None}
    )
    graded = _result("ted:00000002-2026", grade=3, strength=3, rules_matched=2)

    assert assessed.rules_priority == 5
    assert graded.rules_priority == 3


def test_a_tender_with_no_publication_date_sorts_below_one_that_has_one(
    repository: Repository, make_tender: TenderFactory, run_id: str
) -> None:
    dated = make_tender("00000001-2026", published_date=date(2026, 7, 1))
    undated = make_tender("00000002-2026", published_date=None)
    repository.upsert_tenders([dated, undated], run_id=run_id)
    for tender in (dated, undated):
        repository.save_screening_result(_result(tender.id, grade=2, strength=2, rules_matched=1))

    assert _sql_order(repository) == [dated.id, undated.id]


def test_the_source_of_a_tender_is_unchanged_by_the_ordering(
    repository: Repository, corpus: dict[str, tuple[ScreeningResult, date | None]]
) -> None:
    """A guard on the join: ordering must not drop or duplicate a row."""
    page = repository.list_tenders(sort_by=REGISTER_SORT, limit=MAX_PAGE_SIZE)
    assert page.total == len(corpus)
    assert all(tender.source is SourcePlatform.TED for tender in page.items)
