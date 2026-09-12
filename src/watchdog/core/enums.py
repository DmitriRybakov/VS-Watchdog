"""Controlled vocabularies.

These values are written to the database and appear in URLs, so a member's value
must never change once rows exist. Adding a member is safe; renaming one is a
migration. Member values are lower case so they read well in a query string.

Every axis vocabulary carries an ``UNKNOWN`` member on purpose: "not established"
is a real answer here and must never collapse into a low score or an empty string.
"""

from __future__ import annotations

from enum import StrEnum


class SourcePlatform(StrEnum):
    """Where a notice came from. Half of a tender's identity."""

    TED = "ted"
    DOFFIN = "doffin"
    EIB = "eib"
    WORLD_BANK = "world_bank"


class NoticeStage(StrEnum):
    """How far the procurement itself has travelled."""

    PRIOR_INFORMATION = "prior_information"
    MARKET_CONSULTATION = "market_consultation"
    CONTRACT_NOTICE = "contract_notice"
    MODIFICATION = "modification"
    AWARD = "award"
    OTHER = "other"


class ContractNature(StrEnum):
    SERVICES = "services"
    WORKS = "works"
    SUPPLIES = "supplies"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class Domain(StrEnum):
    """Subject matter of the work."""

    HYDROGEN = "hydrogen"
    CCS_CO2 = "ccs_co2"
    OFFSHORE_WIND = "offshore_wind"
    ONSHORE_RENEWABLES = "onshore_renewables"
    GRID_TRANSMISSION = "grid_transmission"
    INDUSTRIAL_DECARB = "industrial_decarb"
    RENEWABLE_FUELS = "renewable_fuels"
    ENERGY_OTHER = "energy_other"
    OUT_OF_SCOPE = "out_of_scope"
    UNKNOWN = "unknown"


class ServiceType(StrEnum):
    """What the buyer is actually asking someone to do."""

    ADVISORY = "advisory"
    EARLY_PHASE_STUDY = "early_phase_study"
    TECHNO_ECONOMIC = "techno_economic"
    DUE_DILIGENCE = "due_diligence"
    TECHNICAL_ASSISTANCE = "technical_assistance"
    MARKET_ASSESSMENT = "market_assessment"
    ESG_LCA = "esg_lca"
    DESIGN_ENGINEERING = "design_engineering"
    EXECUTION_EPC = "execution_epc"
    SUPPLY = "supply"
    OPERATIONS = "operations"
    UNKNOWN = "unknown"


class DecisionStage(StrEnum):
    """Where the buyer's project sits on the way to a major commitment."""

    CONCEPT_OPTION = "concept_option"
    PRE_FEED_FEASIBILITY = "pre_feed_feasibility"
    FEED_DEFINITION = "feed_definition"
    EXECUTION = "execution"
    OPERATIONS = "operations"
    UNKNOWN = "unknown"


class Band(StrEnum):
    """What the register does with a screened tender."""

    SHORTLIST = "shortlist"
    REVIEW = "review"
    ARCHIVE = "archive"


class Verdict(StrEnum):
    """A colleague's judgement. Never written by an automated process."""

    RELEVANT = "relevant"
    NOT_RELEVANT = "not_relevant"
    UNSURE = "unsure"


class BidRoute(StrEnum):
    """How we would go after it, if at all."""

    NOT_ASSESSED = "not_assessed"
    DIRECT_BID = "direct_bid"
    PARTNER_ROUTE = "partner_route"
    WATCH = "watch"
    NO_BID = "no_bid"


class RuleSignal(StrEnum):
    """What kind of evidence a deterministic rule found."""

    DOMAIN = "domain"
    ACTIVITY = "activity"
    STAGE = "stage"
    EXCLUSION = "exclusion"


class Polarity(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class RulesRoute(StrEnum):
    """The rules stage routes; it never rejects anything on its own."""

    ASSESS = "assess"
    ARCHIVE_CANDIDATE = "archive_candidate"


class RunKind(StrEnum):
    INGEST = "ingest"
    SCREEN = "screen"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
