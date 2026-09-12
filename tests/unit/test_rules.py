"""The deterministic stage, against the rule set we actually ship.

These tests read config/rules.yaml rather than a hand-built fixture on purpose:
the behaviour worth protecting is what the shipped vocabulary does to a real
notice. An edit that lets HVAC mean high-voltage in a ventilation contract, or
lets a bilingual notice count twice, should fail here.
"""

from __future__ import annotations

import pytest

from watchdog.core.enums import Domain, Polarity, RuleSignal, RulesRoute
from watchdog.core.models import Tender, TextBlock
from watchdog.screening import RuleEngine, RulesConfig, normalise


def notice(
    *blocks: tuple[str, str, str],
    cpv_all: list[str] | None = None,
    source_id: str = "00000001-2026",
) -> Tender:
    """A tender that is nothing but the text and the codes a rule can read.

    ``title`` is filled with something that would match several rules if it were
    ever screened. It is not, and a test that starts failing because of it has
    found a real bug.
    """
    return Tender(
        source="ted",
        source_id=source_id,
        title="Norway - Feasibility study, advisory service, analysis - composed title",
        cpv_all=cpv_all or [],
        screening_blocks=[
            TextBlock(field=field, language=language, text=text) for field, language, text in blocks
        ],
    )


def matched_rules(engine: RuleEngine, tender: Tender) -> set[str]:
    return {match.rule_id for match in engine.evaluate(tender).matches}


# --------------------------------------------------------------- normalisation

NORMALISATION_CASES = [
    ("hyphen and space are the same word break", "Power-to-X readiness", "domain_hydrogen"),
    ("unicode hyphen too", "Power\u2010to\u2010X readiness", "domain_hydrogen"),
    ("spaced form", "power to x readiness", "domain_hydrogen"),
    ("accents are stripped", "Production d'hydrog\u00e8ne vert", "domain_hydrogen"),
    ("accent already absent", "Production d'hydrogene vert", "domain_hydrogen"),
    ("subscript H2", "Studie zur H\u2082-Infrastruktur", "domain_hydrogen"),
    ("case is folded", "MACHBARKEITSSTUDIE", "activity_early_phase_study"),
    (
        "eszett folds to ss",
        "Stra\u00dfenbau Planungsleistungen",
        "exclusion_roads_transport_works",
    ),
    (
        "typographic apostrophe",
        "Assistance \u00e0 ma\u00eetrise d\u2019ouvrage",
        "activity_advisory",
    ),
    ("collapsed whitespace", "offshore    wind  farm", "domain_offshore_wind"),
]


@pytest.mark.parametrize(
    ("case", "text", "expected_rule"),
    NORMALISATION_CASES,
    ids=[case[0] for case in NORMALISATION_CASES],
)
def test_normalisation_variants_match_the_same_rule(
    rule_engine: RuleEngine, case: str, text: str, expected_rule: str
) -> None:
    assert expected_rule in matched_rules(rule_engine, notice(("title-proc", "eng", text)))


def test_normalising_does_not_alter_the_stored_text(rule_engine: RuleEngine) -> None:
    original = "Produktion von H\u2082 \u2014 Machbarkeitsstudie f\u00fcr Stra\u00dfenbahnen"
    tender = notice(("title-proc", "deu", original))

    rule_engine.evaluate(tender)

    assert tender.screening_blocks[0].text == original
    assert normalise(original).text == "produktion von h2 machbarkeitsstudie fur strassenbahnen"


# ------------------------------------------------------------ token boundaries

BOUNDARY_CASES = [
    (
        "ECO2000 is not CO2",
        "Maintenance du compresseur ECO2000 et capture des poussi\u00e8res",
        False,
    ),
    ("CO2 with capture is CO2", "\u00c9tude de capture du CO2 industriel", True),
    ("subscript CO2 counts", "Abscheidung von CO\u2082 - carbon capture", True),
    ("hyphenated CO2 counts", "CO2-Abscheidung und Speicherung, carbon capture", True),
]


@pytest.mark.parametrize(
    ("case", "text", "expected"),
    BOUNDARY_CASES,
    ids=[case[0] for case in BOUNDARY_CASES],
)
def test_co2_matches_on_token_boundaries_only(
    rule_engine: RuleEngine, case: str, text: str, expected: bool
) -> None:
    hit = "domain_ccs_co2" in matched_rules(rule_engine, notice(("title-proc", "fra", text)))
    assert hit is expected


def test_h2_does_not_fire_inside_another_token(rule_engine: RuleEngine) -> None:
    text = "Analyse von H2O-Proben und H2SO4-Lagerung"
    assert "domain_hydrogen" not in matched_rules(rule_engine, notice(("title-proc", "deu", text)))


# ----------------------------------------------------------------- ambiguity


def test_hvac_without_electrical_context_is_not_a_grid_domain(rule_engine: RuleEngine) -> None:
    """Unqualified, HVAC is heating and ventilation. This is the old sheet's worst
    false positive and the reason acronyms carry required context."""
    tender = notice(
        ("title-proc", "nld", "Vervanging van de HVAC installatie in het gemeentehuis"),
        ("description-proc", "nld", "Onderhoud van verwarming, ventilatie en koeling."),
    )

    result = rule_engine.evaluate(tender)

    assert "domain_grid_hvac" not in {match.rule_id for match in result.matches}
    assert Domain.GRID_TRANSMISSION not in result.domains_hit


def test_hvac_with_electrical_context_is_a_grid_domain(rule_engine: RuleEngine) -> None:
    tender = notice(
        ("title-proc", "eng", "HVAC converter station design support for the transmission link"),
    )

    result = rule_engine.evaluate(tender)

    assert "domain_grid_hvac" in {match.rule_id for match in result.matches}
    assert Domain.GRID_TRANSMISSION in result.domains_hit


def test_context_does_not_cross_a_block_boundary(rule_engine: RuleEngine) -> None:
    """A Flemish buyer's two language variants are two independent texts. A context
    word in the French one cannot qualify an acronym in the Dutch one."""
    tender = notice(
        ("title-proc", "nld", "Vervanging van de HVAC installatie"),
        ("title-proc", "fra", "Renovation de la substation electrique"),
    )

    matched = matched_rules(rule_engine, tender)

    assert "domain_grid_hvac" not in matched
    # The French block's own word still counts for the rule it belongs to.
    assert "domain_grid_transmission" in matched


def test_context_within_one_block_is_enough(rule_engine: RuleEngine) -> None:
    tender = notice(
        ("title-proc", "nld", "HVAC en substation werkzaamheden"),
    )

    assert "domain_grid_hvac" in matched_rules(rule_engine, tender)


# ------------------------------------------------------------- prefix aliases

PREFIX_CASES = [
    ("German plural", "Sanierung von Umspannwerke", "domain_grid_transmission", True),
    (
        "German head-initial compound",
        "Umspannwerksanierung im Stadtgebiet",
        "domain_grid_transmission",
        True,
    ),
    (
        "a compound whose head is at the end is still missed",
        "Planung von Umbauten in Bestandsumspannwerken",
        "domain_grid_transmission",
        False,
    ),
    (
        "German compound noun",
        "Wasserstoffinfrastruktur f\u00fcr das Industriegebiet",
        "domain_hydrogen",
        True,
    ),
    ("Polish adjective", "Budowa instalacji fotowoltaicznych", "domain_onshore_renewables", True),
    (
        "French plural",
        "Maintenance des installations photovolta\u00efques",
        "domain_onshore_renewables",
        True,
    ),
    ("Italian plural", "Impianti fotovoltaici sugli edifici", "domain_onshore_renewables", True),
    ("still needs a word start", "Lieferung eines Mikrowasserstoffs", "domain_hydrogen", False),
]


@pytest.mark.parametrize(
    ("case", "text", "expected_rule", "expected"),
    PREFIX_CASES,
    ids=[case[0] for case in PREFIX_CASES],
)
def test_a_prefix_alias_matches_the_word_it_starts(
    rule_engine: RuleEngine, case: str, text: str, expected_rule: str, expected: bool
) -> None:
    """A trailing marker drops the closing boundary only. It never lets a term
    start in the middle of a word, which is what keeps it auditable."""
    hit = expected_rule in matched_rules(rule_engine, notice(("title-proc", "deu", text)))

    assert hit is expected


def test_a_prefix_alias_is_reported_as_written(rule_engine: RuleEngine) -> None:
    """The evidence names the rule's alias, marker and all, so a colleague can see
    that a prefix and not an exact word is what fired."""
    tender = notice(("title-proc", "deu", "Qualifizierungssystem Sanierung von Umspannwerke"))

    match = next(
        m for m in rule_engine.evaluate(tender).matches if m.rule_id == "domain_grid_transmission"
    )

    assert match.alias_matched == "umspannwerk*"
    assert "Umspannwerke" in match.evidence


# ------------------------------------------------------- companion requirement


def test_a_fee_schedule_term_does_not_archive_on_its_own(rule_engine: RuleEngine) -> None:
    """HOAI is the German fee schedule for architects AND engineers. On its own it
    says how a service is priced, not what it is for, so it cannot archive."""
    tender = notice(
        ("title-proc", "deu", "Qualifizierungssystem Sanierung von Anlagen"),
        ("description-proc", "deu", "Planungsleistungen in Anlehnung an die HOAI."),
    )

    result = rule_engine.evaluate(tender)

    assert "exclusion_building_profession" not in {m.rule_id for m in result.matches}
    assert result.route is RulesRoute.ASSESS


def test_a_fee_schedule_term_archives_beside_a_building(rule_engine: RuleEngine) -> None:
    tender = notice(
        ("title-proc", "deu", "Neubau Feuerwehrhaus - Planungsleistungen gem. HOAI"),
    )

    result = rule_engine.evaluate(tender)

    assert "exclusion_building_profession" in {m.rule_id for m in result.matches}
    assert result.route is RulesRoute.ARCHIVE_CANDIDATE


def test_a_companion_may_be_in_another_block(rule_engine: RuleEngine) -> None:
    """Unlike a context word, a companion is a question about the whole notice, so
    it is satisfied anywhere in it - the title names the fee schedule, the
    description names the building."""
    tender = notice(
        ("title-proc", "deu", "Planungsleistungen gem. HOAI 2021, LPH 1-9"),
        ("description-proc", "deu", "Die Gemeinde plant den Neubau einer Schule."),
    )

    assert "exclusion_building_profession" in matched_rules(rule_engine, tender)


def test_capacity_building_does_not_archive_without_an_administration_subject(
    rule_engine: RuleEngine,
) -> None:
    tender = notice(
        ("title-proc", "eng", "Capacity building for the offshore wind permitting team"),
    )

    result = rule_engine.evaluate(tender)

    assert "exclusion_capacity_building_generic" not in {m.rule_id for m in result.matches}
    assert result.route is RulesRoute.ASSESS


def test_technical_assistance_for_capacity_building_is_not_an_activity(
    rule_engine: RuleEngine,
) -> None:
    """The trap word. On TED it is mostly embedded personnel and institutional
    capacity building, which profile.yaml rules out regardless of sector."""
    tender = notice(
        (
            "title-proc",
            "eng",
            "Technical assistance for institutional capacity building in the partner country",
        ),
        (
            "description-proc",
            "eng",
            "Provision of a pool of experts and embedded expert support to the ministry, "
            "including programme administration and procurement of works.",
        ),
    )

    result = rule_engine.evaluate(tender)
    matched = {match.rule_id for match in result.matches}

    assert "activity_technical_assistance" not in matched
    assert "exclusion_capacity_building" in matched
    assert result.route is RulesRoute.ARCHIVE_CANDIDATE


def test_technical_assistance_for_a_study_is_an_activity(rule_engine: RuleEngine) -> None:
    tender = notice(
        ("title-proc", "eng", "Technical assistance for a pre-feasibility study of the grid link"),
    )

    assert "activity_technical_assistance" in matched_rules(rule_engine, tender)


FEED_CASES = [
    ("feedback is not FEED", "Feedback on a hydrogen project", False),
    ("a FEED study is FEED", "FEED study for a hydrogen plant", True),
    ("front end engineering design is FEED", "Front-end engineering design for the plant", True),
    ("a feed-in tariff is not FEED", "Consultancy on feed-in tariff design for solar", False),
    ("animal feed is not FEED", "Supply of animal feed for the municipal farm", False),
    ("boiler feed water is not FEED", "Boiler feed water treatment services", False),
]


@pytest.mark.parametrize(
    ("case", "text", "expected"),
    FEED_CASES,
    ids=[case[0] for case in FEED_CASES],
)
def test_feed_is_only_a_stage_when_it_is_qualified(
    rule_engine: RuleEngine, case: str, text: str, expected: bool
) -> None:
    """FEED is matched only with a qualifier beside it - FEED study, FEED phase,
    front end engineering design. Token boundaries already keep it out of
    "feedback"; what they cannot do is separate engineering FEED from a feed-in
    tariff, because a feed-in tariff notice is an energy notice and satisfies any
    context list containing "design" or "study". The stage axis is an input to the
    score, so a wrong stage here is a wrong input and not a cosmetic label."""
    hit = "stage_feed_definition" in matched_rules(rule_engine, notice(("title-proc", "eng", text)))

    assert hit is expected


# ------------------------------------------------------------- once per tender


def test_a_bilingual_notice_counts_a_rule_once(rule_engine: RuleEngine) -> None:
    """Publishing the same title in Dutch and French is not twice the evidence."""
    tender = notice(
        ("title-proc", "nld", "Haalbaarheidsstudie voor waterstof productie"),
        ("title-proc", "fra", "Etude de faisabilite pour la production d'hydrogene"),
        ("description-proc", "nld", "De studie betreft waterstof en elektrolyse."),
    )

    result = rule_engine.evaluate(tender)

    hydrogen_matches = [m for m in result.matches if m.rule_id == "domain_hydrogen"]
    assert len(hydrogen_matches) == 1
    assert result.domains_hit == [Domain.HYDROGEN]


def test_the_composed_display_title_is_never_screened(rule_engine: RuleEngine) -> None:
    """The composed title carries TED's own CPV label; reading it would make an
    activity rule fire on every notice. See docs/decisions/0002."""
    tender = notice(("title-proc", "pol", "Dostawa mebli biurowych"))

    assert matched_rules(rule_engine, tender) == set()
    assert "Feasibility study" in tender.title


# ------------------------------------------------------------------- evidence


def test_every_match_carries_an_evidence_span(rule_engine: RuleEngine) -> None:
    tender = notice(
        ("title-proc", "eng", "Pre-feasibility study for a green hydrogen and ammonia facility"),
        (
            "description-proc",
            "eng",
            "The consultancy shall deliver a techno-economic assessment, including system "
            "sizing, a production profile and the business case for the offshore wind supply.",
        ),
    )

    result = rule_engine.evaluate(tender)

    assert result.matches
    for match in result.matches:
        assert match.evidence.strip()
        assert normalise(match.alias_matched).text in normalise(match.evidence).text
        assert match.field in {"title-proc", "description-proc"}


def test_evidence_is_cut_from_the_original_characters(rule_engine: RuleEngine) -> None:
    tender = notice(("title-proc", "fra", "\u00c9tude de faisabilit\u00e9 d'un \u00e9lectrolyseur"))

    matches = {m.rule_id: m for m in rule_engine.evaluate(tender).matches}

    assert matches["domain_hydrogen"].alias_matched == "\u00e9lectrolyse*"
    assert "\u00e9lectrolyseur" in matches["domain_hydrogen"].evidence


def test_evidence_is_trimmed_and_marked_where_it_was_cut(rule_engine: RuleEngine) -> None:
    filler = "a" * 400
    tender = notice(("description-proc", "eng", f"{filler} green hydrogen {filler}"))

    match = next(m for m in rule_engine.evaluate(tender).matches if m.rule_id == "domain_hydrogen")

    assert match.evidence.startswith("\u2026")
    assert match.evidence.endswith("\u2026")
    assert len(match.evidence) < 200


def test_an_exclusion_match_is_negative_and_a_domain_match_is_positive(
    rule_engine: RuleEngine,
) -> None:
    tender = notice(("title-proc", "eng", "Cleaning services for the hydrogen laboratory"))

    polarity = {m.rule_id: m.polarity for m in rule_engine.evaluate(tender).matches}

    assert polarity["exclusion_facilities_management"] is Polarity.NEGATIVE
    assert polarity["domain_hydrogen"] is Polarity.POSITIVE


# --------------------------------------------------------------------- routing

ROUTING_CASES = [
    (
        "hospital cleaning archives",
        [("title-proc", "eng", "Provision of cleaning services and hospital catering")],
        [],
        RulesRoute.ARCHIVE_CANDIDATE,
    ),
    (
        "an exclusion beside a domain does not archive",
        [("title-proc", "eng", "Cleaning services for the green hydrogen plant")],
        [],
        RulesRoute.ASSESS,
    ),
    (
        "an energy CPV code stops an exclusion archiving",
        [("title-proc", "ita", "Sistema di qualificazione per servizi di facility management")],
        ["71314200"],
        RulesRoute.ASSESS,
    ),
    (
        "the same notice without the energy code archives",
        [("title-proc", "ita", "Sistema di qualificazione per servizi di facility management")],
        ["79713000"],
        RulesRoute.ARCHIVE_CANDIDATE,
    ),
    (
        "no match at all is assessed, never archived",
        [("title-proc", "ita", "Servizi di supporto per la gestione documentale")],
        [],
        RulesRoute.ASSESS,
    ),
    (
        "a supporting term alone does not stop an exclusion",
        [("title-proc", "eng", "Cleaning services for the energy department offices")],
        [],
        RulesRoute.ARCHIVE_CANDIDATE,
    ),
]


@pytest.mark.parametrize(
    ("case", "blocks", "cpv_all", "expected"),
    ROUTING_CASES,
    ids=[case[0] for case in ROUTING_CASES],
)
def test_routing(
    rule_engine: RuleEngine,
    case: str,
    blocks: list[tuple[str, str, str]],
    cpv_all: list[str],
    expected: RulesRoute,
) -> None:
    assert rule_engine.evaluate(notice(*blocks, cpv_all=cpv_all)).route is expected


def test_cpv_matching_is_hierarchical_not_a_string_prefix(rule_engine: RuleEngine) -> None:
    """71314200 is beneath the configured 71314000, exactly as TED's own filter
    treats it, and ``startswith`` on the padded code would miss it."""
    tender = notice(("title-proc", "ita", "Servizi tecnici"), cpv_all=["71314200"])

    assert rule_engine.evaluate(tender).cpv_match is True
    assert "71314200".startswith("71314000") is False


def test_a_notice_too_thin_to_judge_is_assessed(rule_engine: RuleEngine) -> None:
    """Thin text is not evidence of irrelevance. Silence routes to a human, never
    to the archive - and a notice with no blocks at all cannot match an exclusion."""
    empty = Tender(source="ted", source_id="00000002-2026", title="Norway - Services - x")

    result = rule_engine.evaluate(empty)

    assert result.matches == []
    assert result.has_exclusion is False
    assert result.route is RulesRoute.ASSESS


THIN_TEXT_CASES = [
    ("a fee schedule and nothing else", "Planungsleistungen gem. HOAI 2021"),
    ("a profession and nothing else", "Recrutement d'un architecte"),
    ("work stages and nothing else", "Leistungsphasen 1 bis 9, stufenweise Beauftragung"),
    ("a fee schedule beside our own domain", "HOAI-Planung Umspannwerke"),
]


@pytest.mark.parametrize(
    ("case", "title"),
    THIN_TEXT_CASES,
    ids=[case[0] for case in THIN_TEXT_CASES],
)
def test_a_thin_notice_with_an_exclusion_term_is_still_assessed(
    rule_engine: RuleEngine, case: str, title: str
) -> None:
    """The realistic thin notice is not an empty one: it is a title carrying a
    professional term and no usable description. A two-line notice means we do not
    know, which is a human's question and not an archive."""
    result = rule_engine.evaluate(notice(("title-proc", "deu", title)))

    assert result.route is RulesRoute.ASSESS


def test_an_unrelated_cpv_code_is_not_a_match(rule_engine: RuleEngine) -> None:
    tender = notice(("title-proc", "ita", "Servizi tecnici"), cpv_all=["79713000", "45233120"])

    assert rule_engine.evaluate(tender).cpv_match is False


def test_the_result_carries_the_rules_version_it_was_judged_under(
    rule_engine: RuleEngine,
) -> None:
    result = rule_engine.evaluate(notice(("title-proc", "eng", "Hydrogen feasibility study")))

    assert result.rules_version == rule_engine.config.rules_version
    assert result.tender_id == "ted:00000001-2026"


def test_a_supporting_rule_is_recorded_but_establishes_no_domain(
    rule_engine: RuleEngine,
) -> None:
    tender = notice(("title-proc", "eng", "Framework for energy and power advisory support"))

    result = rule_engine.evaluate(tender)
    matched = {match.rule_id for match in result.matches}

    assert "domain_supporting_terms" in matched
    assert result.domains_hit == []


def test_an_inactive_rule_is_not_compiled(rules_config: RulesConfig) -> None:
    deactivated = rules_config.model_copy(
        update={
            "rules": [
                rule.model_copy(update={"active": False}) if rule.id == "domain_hydrogen" else rule
                for rule in rules_config.rules
            ]
        }
    )

    engine = RuleEngine(deactivated)
    tender = notice(("title-proc", "eng", "Green hydrogen feasibility study"))

    assert "domain_hydrogen" not in matched_rules(engine, tender)


def test_signals_are_reported_with_their_matches(rule_engine: RuleEngine) -> None:
    tender = notice(
        ("title-proc", "eng", "Pre-FEED study for an offshore wind and battery storage hub"),
    )

    signals = {match.rule_id: match.signal for match in rule_engine.evaluate(tender).matches}

    assert signals["domain_offshore_wind"] is RuleSignal.DOMAIN
    assert signals["activity_early_phase_study"] is RuleSignal.ACTIVITY
    assert signals["stage_pre_feed_feasibility"] is RuleSignal.STAGE
