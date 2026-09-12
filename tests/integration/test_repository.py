"""What the register must never do: forget, delete, or overwrite.

These run against an in-memory SQLite database built from the same metadata as
production; the session factory is injected, nothing else changes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from watchdog.core.enums import (
    Band,
    BidRoute,
    ContractNature,
    DecisionStage,
    Domain,
    NoticeStage,
    RunKind,
    RunStatus,
    ServiceType,
    SourcePlatform,
    Verdict,
)
from watchdog.core.models import (
    Assessment,
    AxisScore,
    DetailField,
    Review,
    RulesResult,
    ScreeningResult,
    ScreeningVersions,
    Tender,
    TenderDetail,
    TenderFilters,
    TextBlock,
)
from watchdog.storage.db import create_db_engine, create_session_factory
from watchdog.storage.repository import Repository
from watchdog.storage.tables import Base

VERSIONS = ScreeningVersions(
    rules_version="rules-1",
    policy_version="policy-1",
    profile_version="profile-1",
    prompt_version=None,
)


def test_an_unmigrated_database_is_reported_rather_than_raised() -> None:
    # The first thing a colleague does on a new machine is run a command before
    # the migration. That must be a sentence, not a database error.
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    try:
        check = Repository(create_session_factory(engine)).check_schema()
    finally:
        engine.dispose()

    assert check.ok is False
    assert check.empty is True
    assert "tender" in check.missing_tables


def test_a_database_one_migration_behind_names_the_column_it_is_missing() -> None:
    # The case that produced 16KB of SQL: every table present, one column short.
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE tender DROP COLUMN first_seen_run_id"))
        check = Repository(create_session_factory(engine)).check_schema()
    finally:
        engine.dispose()

    assert check.ok is False
    assert check.empty is False, "the database exists; it is behind, not absent"
    assert check.missing_tables == []
    assert check.missing_columns == ["tender.first_seen_run_id"]
    assert check.summary == "tender.first_seen_run_id"


def test_a_database_missing_one_table_is_reported_without_querying_it() -> None:
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE quarantine"))
        check = Repository(create_session_factory(engine)).check_schema()
    finally:
        engine.dispose()

    assert check.missing_tables == ["quarantine"]
    assert check.empty is False


def test_a_migrated_database_reports_itself_ready(repository: Repository) -> None:
    assert repository.check_schema().ok is True


def screening_for(tender: Tender, *, score: int = 4, band: Band = Band.REVIEW) -> ScreeningResult:
    return ScreeningResult(
        tender_id=tender.id,
        score=score,
        band=band,
        confidence=0.6,
        confidence_reasons=["keyword evidence only"],
        reason_codes=["domain_hydrogen"],
        rules=RulesResult(tender_id=tender.id, rules_version=VERSIONS.rules_version),
        explanation="Rules found a hydrogen domain match in the title.",
        screened_content_hash=tender.content_hash,
        provider="disabled",
        model=None,
        prompt_version=VERSIONS.prompt_version,
        rules_version=VERSIONS.rules_version,
        policy_version=VERSIONS.policy_version,
        profile_version=VERSIONS.profile_version,
    )


# ------------------------------------------------------------------ upserting


def test_upserting_the_same_notice_with_a_new_deadline_updates_and_logs_one_change(
    repository: Repository, make_tender, run_id: str
) -> None:
    first = make_tender()
    repository.upsert_tenders([first], run_id=run_id)

    later = first.model_copy(
        update={
            "deadline": datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
            "source_version": "1",
            "last_seen_at": datetime(2026, 3, 9, 6, 0, tzinfo=UTC),
        }
    )
    stats = repository.upsert_tenders([later], run_id=run_id)

    assert (stats.new, stats.updated, stats.unchanged) == (0, 1, 0)

    stored = repository.get_tender(first.id)
    assert stored is not None
    assert stored.deadline == datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    assert stored.first_seen_at == first.first_seen_at
    assert stored.last_seen_at == datetime(2026, 3, 9, 6, 0, tzinfo=UTC)

    changes = repository.list_changes(first.id)
    assert len(changes) == 1
    assert changes[0].field == "deadline"
    assert changes[0].old_value == "2026-04-15T12:00:00+00:00"
    assert changes[0].new_value == "2026-05-01T12:00:00+00:00"


def test_re_ingesting_an_unchanged_notice_only_moves_last_seen_at(
    repository: Repository, make_tender, run_id: str
) -> None:
    tender = make_tender()
    repository.upsert_tenders([tender], run_id=run_id)

    seen_again = tender.model_copy(update={"last_seen_at": datetime(2026, 3, 9, 6, 0, tzinfo=UTC)})
    stats = repository.upsert_tenders([seen_again], run_id=run_id)

    assert (stats.new, stats.updated, stats.unchanged) == (0, 0, 1)
    assert repository.list_changes(tender.id) == []

    stored = repository.get_tender(tender.id)
    assert stored is not None
    assert stored.last_seen_at == datetime(2026, 3, 9, 6, 0, tzinfo=UTC)


def test_a_notice_that_disappears_from_the_source_is_kept(
    repository: Repository, make_tender, run_id: str
) -> None:
    staying = make_tender("aaa-2026")
    vanishing = make_tender("bbb-2026")
    repository.upsert_tenders([staying, vanishing], run_id=run_id)

    # The next run returns only one of them; the other must survive untouched.
    repository.upsert_tenders([staying], run_id=run_id)

    assert repository.get_tender(vanishing.id) is not None
    assert repository.list_tenders().total == 2


def test_an_unrelated_change_does_not_write_a_change_row(
    repository: Repository, make_tender, run_id: str
) -> None:
    tender = make_tender()
    repository.upsert_tenders([tender], run_id=run_id)

    stats = repository.upsert_tenders(
        [tender.model_copy(update={"buyer_name": "Statsbygg (Oslo)"})], run_id=run_id
    )

    assert stats.updated == 1
    assert repository.list_changes(tender.id) == []


def test_source_text_is_stored_exactly_as_received(
    repository: Repository, make_tender, run_id: str
) -> None:
    original = "Tjenester for hydrogenproduksjon – forprosjekt (FEED)"
    repository.upsert_tenders([make_tender(title=original)], run_id=run_id)

    stored = repository.list_tenders().items[0]
    assert stored.title == original


def test_money_keeps_its_currency_and_its_value(
    repository: Repository, make_tender, run_id: str
) -> None:
    repository.upsert_tenders([make_tender()], run_id=run_id)

    stored = repository.list_tenders().items[0]
    assert stored.estimated_value == Decimal("250000.00")
    assert stored.currency == "NOK"


def test_a_publication_date_is_the_same_day_in_every_timezone(
    repository: Repository, make_tender, run_id: str
) -> None:
    repository.upsert_tenders([make_tender(published_date=date(2026, 3, 1))], run_id=run_id)

    stored = repository.list_tenders().items[0]

    assert stored.published_date == date(2026, 3, 1)
    assert not isinstance(stored.published_date, datetime)
    # Stored as midnight UTC instead, a reader five hours west would read 28 February.
    new_york = timezone(timedelta(hours=-5))
    assert datetime(2026, 3, 1, tzinfo=UTC).astimezone(new_york).date() == date(2026, 2, 28)


def test_a_publication_date_filter_uses_calendar_days(
    repository: Repository, make_tender, run_id: str
) -> None:
    repository.upsert_tenders(
        [
            make_tender("feb", published_date=date(2026, 2, 27)),
            make_tender("mar", published_date=date(2026, 3, 1)),
        ],
        run_id=run_id,
    )

    page = repository.list_tenders(TenderFilters(published_from=date(2026, 3, 1)))

    assert [tender.source_id for tender in page.items] == ["mar"]


# ------------------------------------------------------------------- listing


def test_filters_pagination_and_total(repository: Repository, make_tender, run_id: str) -> None:
    repository.upsert_tenders(
        [
            make_tender("no-1", buyer_country="NO"),
            make_tender("no-2", buyer_country="NO"),
            make_tender("de-1", buyer_country="DE"),
        ],
        run_id=run_id,
    )

    page = repository.list_tenders(
        TenderFilters(buyer_country="NO"), sort_by="title", descending=False, limit=1, offset=0
    )

    assert page.total == 2
    assert len(page.items) == 1

    second = repository.list_tenders(
        TenderFilters(buyer_country="NO"), sort_by="title", descending=False, limit=1, offset=1
    )
    assert second.total == 2
    assert second.items[0].id != page.items[0].id


def test_sorting_by_deadline_puts_unknown_deadlines_last(
    repository: Repository, make_tender, run_id: str
) -> None:
    repository.upsert_tenders(
        [
            make_tender("late", deadline=datetime(2026, 6, 1, 12, 0, tzinfo=UTC)),
            make_tender("early", deadline=datetime(2026, 4, 1, 12, 0, tzinfo=UTC)),
            make_tender("unknown", deadline=None),
        ],
        run_id=run_id,
    )

    ordered = repository.list_tenders(sort_by="deadline", descending=False).items

    assert [tender.source_id for tender in ordered] == ["early", "late", "unknown"]


def test_text_filter_matches_title_or_description(
    repository: Repository, make_tender, run_id: str
) -> None:
    repository.upsert_tenders(
        [
            make_tender("h", title="Hydrogen feasibility study"),
            make_tender("w", title="Offshore wind survey", description="Seabed survey."),
        ],
        run_id=run_id,
    )

    page = repository.list_tenders(TenderFilters(text="seabed"))

    assert [tender.source_id for tender in page.items] == ["w"]


def test_a_services_filter_keeps_a_works_notice_that_also_buys_services(
    repository: Repository, make_tender, run_id: str
) -> None:
    """Modelled on 563282-2026: main nature works, natures [services, works].

    TED's own filter keeps this notice because services appears somewhere on it.
    Filtering our scalar instead of the list would silently drop it.
    """
    mixed = make_tender(
        "563282-2026",
        contract_natures=[ContractNature.SERVICES, ContractNature.WORKS],
    ).model_copy(update={"contract_nature": ContractNature.WORKS})
    works_only = make_tender("works-only", contract_natures=[ContractNature.WORKS]).model_copy(
        update={"contract_nature": ContractNature.WORKS}
    )

    repository.upsert_tenders([mixed, works_only], run_id=run_id)

    page = repository.list_tenders(TenderFilters(contract_nature=ContractNature.SERVICES))

    assert [tender.source_id for tender in page.items] == ["563282-2026"]
    assert page.items[0].contract_nature is ContractNature.WORKS


def test_a_services_filter_still_excludes_a_notice_with_no_services(
    repository: Repository, make_tender, run_id: str
) -> None:
    repository.upsert_tenders(
        [
            make_tender("supplies", contract_natures=[ContractNature.SUPPLIES]),
            make_tender("services", contract_natures=[ContractNature.SERVICES]),
        ],
        run_id=run_id,
    )

    page = repository.list_tenders(TenderFilters(contract_nature=ContractNature.SERVICES))

    assert [tender.source_id for tender in page.items] == ["services"]


def test_a_nature_filter_cannot_match_a_substring_of_another_value(
    repository: Repository, make_tender, run_id: str
) -> None:
    repository.upsert_tenders(
        [make_tender("w", contract_natures=[ContractNature.WORKS])], run_id=run_id
    )

    # "works" must not be found by a filter for "work".
    page = repository.list_tenders(TenderFilters(contract_nature=ContractNature.WORKS))
    assert len(page.items) == 1


def test_the_text_filter_also_searches_the_buyers_own_title(
    repository: Repository, make_tender, run_id: str
) -> None:
    repository.upsert_tenders(
        [
            make_tender(
                "nl",
                title="Belgium - Business and management consultancy - STRATEGISCHE ONDERSTEUNING",
                title_native="STRATEGISCHE, INHOUDELIJKE EN PROJECTMATIGE ONDERSTEUNING",
                description=None,
            ),
            make_tender("other", title="Something else", title_native="Iets anders"),
        ],
        run_id=run_id,
    )

    page = repository.list_tenders(TenderFilters(text="projectmatige"))

    assert [tender.source_id for tender in page.items] == ["nl"]


def test_where_the_work_happens_filters_separately_from_who_is_buying(
    repository: Repository, make_tender, run_id: str
) -> None:
    """Modelled on 619675-2026: a French buyer procuring a study for Angola."""
    repository.upsert_tenders(
        [
            make_tender("angola", buyer_country="FRA", performance_countries=["AGO"]),
            make_tender("france", buyer_country="FRA", performance_countries=["FRA"]),
        ],
        run_id=run_id,
    )

    by_buyer = repository.list_tenders(TenderFilters(buyer_country="FRA"))
    assert {tender.source_id for tender in by_buyer.items} == {"angola", "france"}

    by_place = repository.list_tenders(TenderFilters(place_of_performance_country="AGO"))
    assert [tender.source_id for tender in by_place.items] == ["angola"]


def test_a_notice_spanning_several_countries_is_found_by_any_of_them(
    repository: Repository, make_tender, run_id: str
) -> None:
    repository.upsert_tenders(
        [
            make_tender("maghreb", performance_countries=["MAR", "DZA", "TUN"]),
            make_tender("norway", performance_countries=["NOR"]),
        ],
        run_id=run_id,
    )

    for code in ("MAR", "DZA", "TUN"):
        page = repository.list_tenders(TenderFilters(place_of_performance_country=code))
        assert [tender.source_id for tender in page.items] == ["maghreb"], code

    assert repository.get_tender("ted:maghreb").multi_country is True


def test_the_performance_country_survives_a_round_trip(
    repository: Repository, make_tender, run_id: str
) -> None:
    repository.upsert_tenders(
        [make_tender("a", performance_countries=["AGO", "MAR"])], run_id=run_id
    )

    stored = repository.get_tender("ted:a")

    assert stored is not None
    assert stored.place_of_performance_country == ["AGO", "MAR"]


def test_an_unknown_sort_key_is_rejected(repository: Repository) -> None:
    with pytest.raises(ValueError, match="unknown sort key"):
        repository.list_tenders(sort_by="buyer_name; drop table tender")


def test_band_filter_reads_the_latest_screening(
    repository: Repository, make_tender, run_id: str
) -> None:
    shortlisted = make_tender("s-1")
    archived = make_tender("a-1")
    repository.upsert_tenders([shortlisted, archived], run_id=run_id)
    repository.save_screening_result(screening_for(shortlisted, score=5, band=Band.SHORTLIST))
    repository.save_screening_result(screening_for(archived, score=1, band=Band.ARCHIVE))

    page = repository.list_tenders(TenderFilters(band=Band.SHORTLIST))

    assert [tender.source_id for tender in page.items] == ["s-1"]


# ----------------------------------------------------------------- screening


def test_a_screening_result_never_overwrites_the_previous_one(
    repository: Repository, make_tender, run_id: str
) -> None:
    tender = make_tender()
    repository.upsert_tenders([tender], run_id=run_id)

    first = repository.save_screening_result(
        screening_for(tender, score=2, band=Band.REVIEW).model_copy(
            update={"created_at": datetime(2026, 3, 2, 7, 0, tzinfo=UTC)}
        )
    )
    repository.save_screening_result(
        screening_for(tender, score=5, band=Band.SHORTLIST).model_copy(
            update={"created_at": datetime(2026, 3, 5, 7, 0, tzinfo=UTC)}
        )
    )

    history = repository.screening_history(tender.id)
    assert [result.score for result in history] == [5, 2]
    assert [result.superseded for result in history] == [False, True]
    # The old row still says exactly what it said before.
    assert history[1].score == first.score
    assert history[1].band == first.band
    assert history[1].explanation == first.explanation

    latest = repository.latest_screening_for([tender.id])
    assert latest[tender.id].score == 5


def test_an_assessment_survives_the_round_trip(
    repository: Repository, make_tender, run_id: str
) -> None:
    tender = make_tender()
    repository.upsert_tenders([tender], run_id=run_id)

    assessment = Assessment(
        domain_fit=AxisScore[Domain](score=5, label=Domain.HYDROGEN, evidence=["hydrogen"]),
        service_fit=AxisScore[ServiceType](score=4, label=ServiceType.EARLY_PHASE_STUDY),
        stage_fit=AxisScore[DecisionStage](score=None, label=DecisionStage.UNKNOWN),
        negative_signals=[],
        missing_information=["No stage stated in the notice"],
        short_reason="Hydrogen pre-FEED study, stage not established.",
    )
    repository.save_screening_result(
        screening_for(tender).model_copy(update={"assessment": assessment, "ai_enabled": True})
    )

    stored = repository.latest_screening_for([tender.id])[tender.id]
    assert stored.assessment is not None
    assert stored.assessment.domain_fit.label is Domain.HYDROGEN
    # "Not established" stays None; it must never arrive back as 0.
    assert stored.assessment.stage_fit.score is None


def test_ids_needing_screening_reacts_to_a_version_bump(
    repository: Repository, make_tender, run_id: str
) -> None:
    tender = make_tender()
    repository.upsert_tenders([tender], run_id=run_id)
    repository.save_screening_result(screening_for(tender))

    assert repository.ids_needing_screening(VERSIONS, "disabled", None) == []

    bumped = VERSIONS.model_copy(update={"policy_version": "policy-2"})
    assert repository.ids_needing_screening(bumped, "disabled", None) == [tender.id]


def test_turning_the_model_on_makes_every_rules_only_result_stale(
    repository: Repository, make_tender, run_id: str
) -> None:
    tender = make_tender()
    repository.upsert_tenders([tender], run_id=run_id)
    repository.save_screening_result(screening_for(tender))

    assert repository.ids_needing_screening(VERSIONS, "azure_openai", "gpt-4o") == [tender.id]


def test_a_corrected_notice_needs_re_screening(
    repository: Repository, make_tender, run_id: str
) -> None:
    tender = make_tender()
    repository.upsert_tenders([tender], run_id=run_id)
    repository.save_screening_result(screening_for(tender))

    corrected = tender.model_copy(
        update={
            "screening_blocks": [
                TextBlock(field="description-proc", language="eng", text="Corrected scope.")
            ]
        }
    )
    repository.upsert_tenders([corrected], run_id=run_id)

    assert repository.ids_needing_screening(VERSIONS, "disabled", None) == [tender.id]


def test_a_corrected_language_variant_needs_re_screening(
    repository: Repository, make_tender, run_id: str
) -> None:
    # The hash covers every block, so a correction in any language makes the
    # existing result stale rather than leaving it looking current.
    dutch = TextBlock(field="title-proc", language="nld", text="Haalbaarheidsstudie")
    french = TextBlock(field="title-proc", language="fra", text="Etude de faisabilite")

    tender = make_tender().model_copy(update={"screening_blocks": [dutch, french]})
    repository.upsert_tenders([tender], run_id=run_id)
    repository.save_screening_result(screening_for(tender))

    assert repository.ids_needing_screening(VERSIONS, "disabled", None) == []

    corrected = tender.model_copy(
        update={
            "screening_blocks": [
                dutch,
                TextBlock(
                    field="title-proc", language="fra", text="Etude de faisabilite, corrigee"
                ),
            ]
        }
    )
    repository.upsert_tenders([corrected], run_id=run_id)

    assert repository.ids_needing_screening(VERSIONS, "disabled", None) == [tender.id]


def test_a_new_cpv_code_needs_re_screening(
    repository: Repository, make_tender, run_id: str
) -> None:
    tender = make_tender()
    repository.upsert_tenders([tender], run_id=run_id)
    repository.save_screening_result(screening_for(tender))

    repository.upsert_tenders(
        [tender.model_copy(update={"cpv_all": [*tender.cpv_all, "09330000"]})], run_id=run_id
    )

    assert repository.ids_needing_screening(VERSIONS, "disabled", None) == [tender.id]


def test_a_never_screened_tender_needs_screening(
    repository: Repository, make_tender, run_id: str
) -> None:
    tender = make_tender()
    repository.upsert_tenders([tender], run_id=run_id)

    assert repository.ids_needing_screening(VERSIONS, "disabled", None) == [tender.id]


# ------------------------------------------------------- reviews and details


def test_a_review_round_trips_and_re_screening_leaves_it_alone(
    repository: Repository, make_tender, run_id: str
) -> None:
    tender = make_tender()
    repository.upsert_tenders([tender], run_id=run_id)
    repository.save_review(
        Review(
            tender_id=tender.id,
            verdict=Verdict.RELEVANT,
            bid_route=BidRoute.PARTNER_ROUTE,
            owner="BD colleague",
            next_action="Contact partner before 1 April",
            note="Fits the mandate.",
            reviewed_by="colleague@example.com",
            reviewed_at=datetime(2026, 3, 3, 10, 0, tzinfo=UTC),
        )
    )

    repository.save_screening_result(screening_for(tender, score=1, band=Band.ARCHIVE))

    review = repository.get_review(tender.id)
    assert review is not None
    assert review.verdict is Verdict.RELEVANT
    assert review.bid_route is BidRoute.PARTNER_ROUTE
    assert review.reviewed_at == datetime(2026, 3, 3, 10, 0, tzinfo=UTC)


def test_changing_your_mind_keeps_the_earlier_review(
    repository: Repository, make_tender, run_id: str
) -> None:
    tender = make_tender()
    repository.upsert_tenders([tender], run_id=run_id)
    repository.save_review(
        Review(
            tender_id=tender.id,
            verdict=Verdict.NOT_RELEVANT,
            bid_route=BidRoute.NO_BID,
            note="Looks like grid maintenance, not advisory.",
            reviewed_by="first@example.com",
            reviewed_at=datetime(2026, 3, 3, 10, 0, tzinfo=UTC),
        )
    )

    repository.save_review(
        Review(
            tender_id=tender.id,
            verdict=Verdict.RELEVANT,
            bid_route=BidRoute.DIRECT_BID,
            note="Clarification says pre-FEED advisory after all.",
            reviewed_by="second@example.com",
            reviewed_at=datetime(2026, 3, 6, 9, 0, tzinfo=UTC),
        )
    )

    current = repository.get_review(tender.id)
    assert current is not None
    assert current.verdict is Verdict.RELEVANT
    assert current.superseded is False

    history = repository.list_reviews(tender.id)
    assert [review.verdict for review in history] == [Verdict.RELEVANT, Verdict.NOT_RELEVANT]
    # The earlier decision keeps its verdict, its reasoning and its author.
    assert history[1].note == "Looks like grid maintenance, not advisory."
    assert history[1].bid_route is BidRoute.NO_BID
    assert history[1].reviewed_by == "first@example.com"
    assert history[1].superseded is True


def test_detail_fields_round_trip_with_their_source_reference(
    repository: Repository, make_tender, run_id: str
) -> None:
    tender = make_tender()
    repository.upsert_tenders([tender], run_id=run_id)
    repository.save_detail(
        TenderDetail(
            tender_id=tender.id,
            fields={
                "scope": DetailField(value="Pre-FEED study", source_ref="section 2.1"),
                "budget": DetailField(value=None, source_ref=None),
            },
            extracted_at=datetime(2026, 3, 4, 8, 0, tzinfo=UTC),
            model="gpt-4o",
            prompt_version="detail-1",
        )
    )

    detail = repository.get_detail(tender.id)
    assert detail is not None
    assert detail.fields["scope"].source_ref == "section 2.1"
    # An unknown fact stays None, not "".
    assert detail.fields["budget"].value is None


# ------------------------------------------------------ runs and watermarks


def test_a_run_records_its_window_counts_and_status(repository: Repository) -> None:
    run = repository.start_run(
        RunKind.INGEST,
        source=SourcePlatform.TED,
        window_from=datetime(2026, 3, 1, tzinfo=UTC),
        window_to=datetime(2026, 3, 8, tzinfo=UTC),
    )
    assert run.status is RunStatus.RUNNING

    finished = repository.finish_run(
        run.run_id,
        status=RunStatus.PARTIAL,
        counts={"fetched": 120, "new": 4},
        errors=["page 3 timed out"],
        watermark_advanced=False,
    )

    assert finished.status is RunStatus.PARTIAL
    assert finished.counts == {"fetched": 120, "new": 4}
    assert finished.errors == ["page 3 timed out"]
    assert finished.finished_at is not None
    assert finished.window_from == datetime(2026, 3, 1, tzinfo=UTC)


def test_finishing_an_unknown_run_is_an_error(repository: Repository) -> None:
    with pytest.raises(ValueError, match="unknown run_id"):
        repository.finish_run("not-a-run", status=RunStatus.SUCCESS)


def test_a_watermark_is_created_then_moved_forward(repository: Repository) -> None:
    assert repository.get_watermark(SourcePlatform.TED) is None

    repository.set_watermark(SourcePlatform.TED, datetime(2026, 3, 8, tzinfo=UTC))
    repository.set_watermark(SourcePlatform.TED, datetime(2026, 3, 15, tzinfo=UTC))

    watermark = repository.get_watermark(SourcePlatform.TED)
    assert watermark is not None
    assert watermark.last_successful_at == datetime(2026, 3, 15, tzinfo=UTC)
    assert watermark.source is SourcePlatform.TED


def test_notice_stage_change_is_logged(repository: Repository, make_tender, run_id: str) -> None:
    tender = make_tender(notice_stage=NoticeStage.PRIOR_INFORMATION)
    repository.upsert_tenders([tender], run_id=run_id)

    repository.upsert_tenders(
        [tender.model_copy(update={"notice_stage": NoticeStage.CONTRACT_NOTICE})], run_id=run_id
    )

    changes = repository.list_changes(tender.id)
    assert [change.field for change in changes] == ["notice_stage"]
    assert changes[0].old_value == "prior_information"
    assert changes[0].new_value == "contract_notice"
