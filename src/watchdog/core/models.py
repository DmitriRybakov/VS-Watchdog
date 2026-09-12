"""The domain model: the shapes every layer agrees on.

Rules that the types here enforce, because getting them wrong is expensive:

- An unknown fact is ``None``. Never ``""``, never ``"Unknown"``, never ``0``.
- A moment in time is a timezone-aware UTC timestamp; a naive one is rejected.
  A publication date is a calendar date, because that is all the source tells us.
  A deadline with no time of day is a date, and its ``deadline`` stays ``None``.
- Money keeps its currency next to it.
- The final score is 1-5; an axis score is 0-5 or ``None`` for "not established".
- Identity is (source, source_id). A title is never an identifier.
- What gets screened is ``screening_blocks``, never the source's own display title.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Protocol

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from watchdog.core.clock import ensure_utc, utc_now
from watchdog.core.enums import (
    Band,
    BidRoute,
    ContractNature,
    DeadlineType,
    DecisionStage,
    Domain,
    NoticeStage,
    Polarity,
    RuleSignal,
    RulesRoute,
    RuleStrength,
    RunKind,
    RunStatus,
    ServiceType,
    SourcePlatform,
    Verdict,
)

# A timestamp that is always UTC-aware. Anything naive fails loudly here rather
# than silently becoming "an hour off" in the register.
UtcDatetime = Annotated[datetime, AfterValidator(ensure_utc)]

# Separator used when hashing screened content: it cannot occur in notice text,
# so "a" + "bc" and "ab" + "c" can never hash the same.
_HASH_SEPARATOR = "\x1f"

# Stands in for a fact we do not have, so a missing description and an empty one
# are different content, exactly as they are different facts.
_HASH_NULL = "\x00"

ID_SEPARATOR = ":"


def make_tender_id(source: SourcePlatform, source_id: str) -> str:
    """The deterministic primary key. Same notice, same id, every run."""
    return f"{source.value}{ID_SEPARATOR}{source_id}"


class TextBlock(BaseModel):
    """One piece of original source text, kept separate from the others.

    Blocks exist so that matching can do two things it cannot do on one joined
    string: count a rule once per tender however many blocks it appears in, and
    satisfy an acronym's context requirement *inside* a block only. Two
    independent texts joined end to end create an adjacency that means nothing,
    and a proximity window straddling the join would fire on it.
    """

    model_config = ConfigDict(from_attributes=True)

    # The source field the text came from, e.g. "title-proc".
    field: str
    # Three-letter language code as the source gave it, or None if it said nothing.
    language: str | None = None
    text: str

    @property
    def label(self) -> str:
        return f"[{self.field}/{self.language or 'und'}]"


def screening_text_from_blocks(blocks: Sequence[TextBlock]) -> str:
    """The blocks as one labelled string, for a prompt or for display.

    Matching reads the blocks, not this. The labels are here so that a person
    reading an explanation can see which field and language a quote came from.
    """
    return "\n\n".join(f"{block.label}\n{block.text}" for block in blocks)


def content_hash(screening_text: str, cpv_all: Sequence[str]) -> str:
    """Hash of exactly what a screening read, so a corrected notice is re-judged.

    It covers the screening text in full - every language variant, every lot
    description - and every CPV code, not just the main one. Hashing less than we
    screen would let a corrected Dutch title leave a stale assessment looking
    current, which is the one failure this field exists to prevent.

    CPV codes are sorted: a reordered list is not a changed notice.
    """
    parts = [
        screening_text if screening_text else _HASH_NULL,
        ",".join(sorted(cpv_all)),
    ]
    return hashlib.sha256(_HASH_SEPARATOR.join(parts).encode("utf-8")).hexdigest()


class Tender(BaseModel):
    """A notice, canonical and source agnostic.

    Source text is kept exactly as received, in its original language. Case,
    accents and hyphens are normalised inside matching code only.

    ``title`` is the source's display title and may be a machine-composed one;
    ``title_native`` is what the buyer actually wrote. Screening reads
    ``screening_blocks``. See docs/decisions/0002.
    """

    model_config = ConfigDict(from_attributes=True)

    source: SourcePlatform
    source_id: str
    source_version: str | None = None
    source_url: str | None = None

    # The display title. On TED this is composed as
    # "<country> - <CPV label> - <buyer's title>" and translated into 24 languages.
    title: str
    title_language: str | None = None
    # The buyer's own title, in the buyer's own language. None means the source
    # did not give one; the notice is still screened on its description.
    title_native: str | None = None
    title_native_language: str | None = None
    description: str | None = None

    buyer_name: str | None = None
    buyer_country: str | None = None
    # The codes exactly as the source gave them, NUTS regions and countries mixed
    # together in one string. Kept whole and unresolved; see docs/decisions/0003.
    place_of_performance: str | None = None
    # Where the work happens, at country level, ISO 3166-1 alpha-3. A different
    # question from who is buying: a French buyer can procure a study for Angola,
    # and a register with only one country field loses that notice.
    place_of_performance_country: list[str] = Field(default_factory=list)

    # TED publishes a calendar date. Storing it as midnight UTC would invent a
    # precision we do not have and read as the day before, west of Greenwich.
    published_date: date | None = None
    # The full moment, only when the source gave a time of day as well as a date.
    deadline: UtcDatetime | None = None
    # The calendar date, set whenever any deadline is known. The register computes
    # urgency from this; a missing time is never filled in with 23:59.
    deadline_date: date | None = None
    # Which source field the deadline was read from, verbatim.
    deadline_source: str | None = None
    deadline_type: DeadlineType = DeadlineType.UNKNOWN

    notice_stage: NoticeStage = NoticeStage.OTHER
    notice_subtype: str | None = None
    # The procedure-level nature, as the source states it.
    contract_nature: ContractNature = ContractNature.UNKNOWN
    # Every nature the notice mentions, deduplicated. A works contract with a
    # services component appears in both. Every filter and facet on "services"
    # uses this list, never the scalar above.
    contract_natures: list[ContractNature] = Field(default_factory=list)

    cpv_main: str | None = None
    cpv_additional: list[str] = Field(default_factory=list)
    # Every code on the notice, procedure and lot level, deduplicated. This is
    # what matching reads: the specific code often exists only on a lot while the
    # procedure-level code is something generic.
    cpv_all: list[str] = Field(default_factory=list)

    estimated_value: Decimal | None = None
    currency: str | None = None

    documents_url: str | None = None
    languages: list[str] = Field(default_factory=list)
    multi_lot: bool = False

    # The original text the screening reads, one block per field and language.
    screening_blocks: list[TextBlock] = Field(default_factory=list)

    first_seen_at: UtcDatetime = Field(default_factory=utc_now)
    last_seen_at: UtcDatetime = Field(default_factory=utc_now)

    # Which ingest run first fetched this notice, and which one saw it last.
    # Watchdog provenance, not a source fact: the repository writes them from the
    # run it was given, and a mapper never sets them.
    first_seen_run_id: str | None = None
    last_seen_run_id: str | None = None

    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def id(self) -> str:
        return make_tender_id(self.source, self.source_id)

    @property
    def screening_text(self) -> str:
        """Every original text variant, labelled. Never the composed display title."""
        return screening_text_from_blocks(self.screening_blocks)

    @property
    def content_hash(self) -> str:
        """Fingerprint of the screening text and every CPV code a screening would judge."""
        return content_hash(self.screening_text, self.cpv_all)

    @property
    def is_services(self) -> bool:
        """True when the notice mentions services anywhere, not only as its main nature."""
        return ContractNature.SERVICES in self.contract_natures

    @property
    def multi_country(self) -> bool:
        """True when the work spans more than one country.

        Derived rather than stored, unlike ``multi_lot``: the list it reads is
        itself stored, so a column here could only ever disagree with it.
        """
        return len(self.place_of_performance_country) > 1

    @property
    def crosses_border_from_buyer(self) -> bool:
        """True when the work happens somewhere other than the buyer's own country."""
        if not self.buyer_country or not self.place_of_performance_country:
            return False
        return self.buyer_country.upper() not in {
            code.upper() for code in self.place_of_performance_country
        }


class TenderChange(BaseModel):
    """One material difference between two versions of the same notice."""

    model_config = ConfigDict(from_attributes=True)

    tender_id: str
    field: str
    old_value: str | None = None
    new_value: str | None = None
    source_version: str | None = None
    detected_at: UtcDatetime = Field(default_factory=utc_now)


class QuarantinedNotice(BaseModel):
    """A notice the mapper could not read, kept whole so it can be recovered later.

    The payload is the point. Counting a failure and moving on would mean that
    once the mapper is fixed the notice is gone unless someone reconstructs the
    window by hand. Identity is (source, source_id), as it is for a tender, so
    meeting the same broken notice again updates this row rather than adding one.

    ``run_id`` is the ingest run that fetched the payload now stored here. A retry
    never claims it: a retry has its own run record and leaves this pointing at
    the run whose failure it is trying to undo.
    """

    model_config = ConfigDict(from_attributes=True)

    source: SourcePlatform
    source_id: str
    # Exactly what the source sent, untouched, so the mapper can be re-run on it.
    payload: dict[str, Any] = Field(default_factory=dict)
    # The most recent reason it could not be read, from an ingest or from a retry.
    error: str
    error_type: str
    run_id: str | None = None
    first_seen_at: UtcDatetime = Field(default_factory=utc_now)
    last_seen_at: UtcDatetime = Field(default_factory=utc_now)
    resolved: bool = False

    @property
    def id(self) -> str:
        """The same identity a tender would have, so the two can never disagree."""
        return make_tender_id(self.source, self.source_id)


class RuleMatch(BaseModel):
    """One alias found in one field, with the text around it kept as evidence."""

    model_config = ConfigDict(from_attributes=True)

    rule_id: str
    signal: RuleSignal
    # The strength the rule carried when it matched, not the strength it carries
    # today: a stored result has to stay readable after the vocabulary moves.
    strength: RuleStrength = RuleStrength.MEDIUM
    alias_matched: str
    field: str
    # About 120 characters of surrounding source text, verbatim.
    evidence: str
    polarity: Polarity = Polarity.POSITIVE


class RulesResult(BaseModel):
    """What the deterministic stage found. Evidence, not a verdict."""

    model_config = ConfigDict(from_attributes=True)

    tender_id: str
    matches: list[RuleMatch] = Field(default_factory=list)
    domains_hit: list[Domain] = Field(default_factory=list)
    cpv_match: bool = False
    has_exclusion: bool = False
    route: RulesRoute = RulesRoute.ASSESS
    rules_version: str


class AxisScore[LabelT: StrEnum](BaseModel):
    """One scoring axis: how well it fits, what it was judged to be, and why."""

    model_config = ConfigDict(from_attributes=True)

    # None means "not established". It must never be silently turned into 0.
    score: int | None = Field(default=None, ge=0, le=5)
    label: LabelT
    evidence: list[str] = Field(default_factory=list)

    @property
    def quotes(self) -> list[str]:
        """The evidence with blanks dropped. A whitespace-only quote is no quote."""
        return [quote for quote in self.evidence if quote.strip()]

    @property
    def is_established(self) -> bool:
        return self.score is not None


class Assessment(BaseModel):
    """The model's contract. Everything it is allowed to tell us, and nothing more."""

    model_config = ConfigDict(from_attributes=True)

    domain_fit: AxisScore[Domain]
    service_fit: AxisScore[ServiceType]
    stage_fit: AxisScore[DecisionStage]
    negative_signals: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    short_reason: str = Field(max_length=240)

    @property
    def axes(self) -> list[tuple[str, AxisScore[Any]]]:
        """The three axes with the names the prompt and the schema call them."""
        return [
            ("domain_fit", self.domain_fit),
            ("service_fit", self.service_fit),
            ("stage_fit", self.stage_fit),
        ]

    @model_validator(mode="after")
    def _require_evidence(self) -> Assessment:
        """A scored axis carries a quote, or it is not an answer we accept.

        Enforced here rather than left to the confidence number: a score with
        nothing behind it cannot be defended to a colleague, and a rule that only
        moves a number is advice. Failing validation sends it back for repair.
        """
        unquoted = [name for name, axis in self.axes if axis.is_established and not axis.quotes]
        if unquoted:
            raise ValueError(
                f"{', '.join(unquoted)}: an axis with a score must carry at least one evidence "
                "quote copied from the notice; use score null and label unknown instead"
            )
        return self


class ScreeningResult(BaseModel):
    """One screening of one tender. Append-only: results are superseded, never edited."""

    model_config = ConfigDict(from_attributes=True)

    tender_id: str
    score: int = Field(ge=1, le=5)
    band: Band
    # What the keyword rules alone claimed, on their own 0-3 scale, when no model
    # was configured. None whenever a model assessed the notice. Kept apart from
    # ``score`` because merging them would make "score 3" ambiguous forever.
    rules_only_score: int | None = Field(default=None, ge=0, le=5)
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_reasons: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)

    # The two evidence counts the register orders on, stored rather than derived
    # from ``rules`` so the order can be done in SQL with paging. Both are facts
    # about what the rules found, recorded as they were when the notice was
    # screened; see RANKING_FIELDS below.
    domain_strength_rank: int = Field(default=0, ge=0, le=3)
    domain_rules_matched: int = Field(default=0, ge=0)

    rules: RulesResult
    assessment: Assessment | None = None
    explanation: str

    ai_enabled: bool = False
    screened_content_hash: str

    provider: str
    model: str | None = None
    prompt_version: str | None = None
    rules_version: str
    policy_version: str
    profile_version: str

    created_at: UtcDatetime = Field(default_factory=utc_now)
    superseded: bool = False

    @property
    def rules_priority(self) -> int:
        """What the register orders on: the rules grade where there is one.

        The grade runs from 0, so it separates "no evidence at all" from "weak
        evidence"; the score cannot, because there an unscreened notice sits at 2
        and a notice that matched something sits at 1.
        """
        return self.rules_only_score if self.rules_only_score is not None else self.score

    @property
    def axes_established(self) -> int:
        """How many of the three axes the score actually rests on.

        Shown beside the score - "5 (1 of 3 axes established)" - because an
        unknown axis is left out of the weighting rather than counted as zero, and
        a bare 5 does not say how much was behind it. Derived rather than stored:
        the assessment it reads is itself stored, so a column could only disagree.
        """
        if self.assessment is None:
            return 0
        return sum(1 for _, axis in self.assessment.axes if axis.is_established)


# The register order, strongest first. One definition, read by the Python report in
# the service layer and translated to columns by the repository. Two orderings that
# drifted apart would put a notice at the top of one view and the middle of another,
# with nothing on screen to say which was right.
#
# It lives here rather than beside the scoring policy because storage may import
# core and nothing else, and the repository has to page on exactly this order.
RANKING_FIELDS: tuple[str, ...] = (
    "rules_priority",
    "domain_strength_rank",
    "domain_rules_matched",
    "published_date",
)


class Rankable(Protocol):
    """What the register order reads: a stored result, or a policy decision."""

    @property
    def rules_priority(self) -> int: ...

    @property
    def domain_strength_rank(self) -> int: ...

    @property
    def domain_rules_matched(self) -> int: ...


def ranking_key(result: Rankable, published_date: date | None) -> tuple[int, int, int, date]:
    """How the register orders notices inside a band, strongest first.

    Sort with ``reverse=True``, having sorted on the tender id first, so that
    notices that tie are still in a fixed sequence and paging cannot show the same
    one twice. A notice with no publication date sorts last, not first.
    """
    return (
        result.rules_priority,
        result.domain_strength_rank,
        result.domain_rules_matched,
        published_date or date.min,
    )


class Review(BaseModel):
    """A colleague's decision. Only a human ever writes one of these.

    Append-only, like a screening result: a new review supersedes the previous one
    and the earlier verdict, note and author stay exactly as they were written.
    """

    model_config = ConfigDict(from_attributes=True)

    tender_id: str
    verdict: Verdict
    bid_route: BidRoute = BidRoute.NOT_ASSESSED
    owner: str | None = None
    next_action: str | None = None
    note: str | None = None
    reviewed_by: str
    reviewed_at: UtcDatetime = Field(default_factory=utc_now)
    superseded: bool = False


class DetailField(BaseModel):
    """One extracted fact and where in the notice it was read."""

    model_config = ConfigDict(from_attributes=True)

    value: str | None = None
    source_ref: str | None = None


class TenderDetail(BaseModel):
    """The summary-template fields for one tender, each with its own source reference.

    The field names are whatever the summary template asks for; they are data, not
    code, so a new template field is a prompt change and not a migration.
    """

    model_config = ConfigDict(from_attributes=True)

    tender_id: str
    fields: dict[str, DetailField] = Field(default_factory=dict)
    extracted_at: UtcDatetime = Field(default_factory=utc_now)
    model: str | None = None
    prompt_version: str | None = None


class Run(BaseModel):
    """One ingest or screening run, so any number on screen can be traced to a run."""

    model_config = ConfigDict(from_attributes=True)

    run_id: str
    kind: RunKind
    source: SourcePlatform | None = None
    window_from: UtcDatetime | None = None
    window_to: UtcDatetime | None = None
    started_at: UtcDatetime = Field(default_factory=utc_now)
    finished_at: UtcDatetime | None = None
    status: RunStatus = RunStatus.RUNNING
    counts: dict[str, int] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    watermark_advanced: bool = False

    # What judged this run, when anything did. None on an ingest, which calls no
    # model: a score cannot be defended without knowing which provider, model and
    # prompt produced it, and those three change independently of each other.
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    # Time spent waiting for the provider, summed over every call in the run.
    latency_ms: int = 0


class Watermark(BaseModel):
    """How far a source has been read successfully."""

    model_config = ConfigDict(from_attributes=True)

    source: SourcePlatform
    last_successful_at: UtcDatetime | None = None
    updated_at: UtcDatetime = Field(default_factory=utc_now)


class ScreeningVersions(BaseModel):
    """The versions a screening was produced under. A bump makes old results stale."""

    rules_version: str
    policy_version: str
    profile_version: str
    prompt_version: str | None = None


class TenderFilters(BaseModel):
    """What the register page can narrow the list by. All filters are optional."""

    source: SourcePlatform | None = None
    buyer_country: str | None = None
    # Where the work happens. Matches against ``Tender.place_of_performance_country``,
    # so a notice performed in several countries is found by any one of them.
    place_of_performance_country: str | None = None
    notice_stage: NoticeStage | None = None
    # Matches against ``Tender.contract_natures``, so a works contract with a
    # services component is found by a services filter. Filtering the scalar
    # would hide it.
    contract_nature: ContractNature | None = None
    published_from: date | None = None
    published_to: date | None = None
    deadline_from: UtcDatetime | None = None
    deadline_to: UtcDatetime | None = None
    # Band and minimum score read the latest screening of each tender.
    band: Band | None = None
    min_score: int | None = Field(default=None, ge=1, le=5)
    # Case-insensitive substring of title or description.
    text: str | None = None


class TenderPage(BaseModel):
    """One page of the register, plus how many rows the filters matched in total."""

    items: list[Tender] = Field(default_factory=list)
    total: int = 0
    limit: int = 0
    offset: int = 0


class UpsertStats(BaseModel):
    """What one ingest did to the register. Nothing is ever deleted."""

    new: int = 0
    updated: int = 0
    unchanged: int = 0


class SchemaCheck(BaseModel):
    """Whether the database has the tables and columns this code expects.

    Checked before a command touches anything, because the alternative is a query
    failing partway through with a message that is mostly SQL. A database one
    migration behind is a different sentence from one that has never been set up,
    but the answer to both is the same: run the migration.
    """

    missing_tables: list[str] = Field(default_factory=list)
    # "table.column" for each column the code expects and the database lacks.
    missing_columns: list[str] = Field(default_factory=list)
    # True when the database holds no tables at all: nothing has ever been run here.
    empty: bool = False

    @property
    def ok(self) -> bool:
        return not self.missing_tables and not self.missing_columns

    @property
    def summary(self) -> str:
        """The first few things that are missing, for a one-line explanation."""
        return ", ".join([*self.missing_tables, *self.missing_columns][:5])
