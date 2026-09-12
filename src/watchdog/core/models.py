"""The domain model: the shapes every layer agrees on.

Rules that the types here enforce, because getting them wrong is expensive:

- An unknown fact is ``None``. Never ``""``, never ``"Unknown"``, never ``0``.
- A moment in time is a timezone-aware UTC timestamp; a naive one is rejected.
  A publication date is a calendar date, because that is all the source tells us.
- Money keeps its currency next to it.
- The final score is 1-5; an axis score is 0-5 or ``None`` for "not established".
- Identity is (source, source_id). A title is never an identifier.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from watchdog.core.clock import ensure_utc, utc_now
from watchdog.core.enums import (
    Band,
    BidRoute,
    ContractNature,
    DecisionStage,
    Domain,
    NoticeStage,
    Polarity,
    RuleSignal,
    RulesRoute,
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


def content_hash(
    title: str,
    description: str | None,
    cpv_main: str | None,
    cpv_additional: Sequence[str],
) -> str:
    """Hash of exactly what a screening judged, so a corrected notice is re-judged.

    Additional CPV codes are sorted: a reordered list is not a changed notice.
    """
    parts = [
        title,
        description if description is not None else _HASH_NULL,
        cpv_main if cpv_main is not None else _HASH_NULL,
        ",".join(sorted(cpv_additional)),
    ]
    return hashlib.sha256(_HASH_SEPARATOR.join(parts).encode("utf-8")).hexdigest()


class Tender(BaseModel):
    """A notice, canonical and source agnostic.

    Source text is kept exactly as received, in its original language. Case,
    accents and hyphens are normalised inside matching code only.
    """

    model_config = ConfigDict(from_attributes=True)

    source: SourcePlatform
    source_id: str
    source_version: str | None = None
    source_url: str | None = None

    title: str
    title_language: str | None = None
    description: str | None = None

    buyer_name: str | None = None
    buyer_country: str | None = None
    place_of_performance: str | None = None

    # TED publishes a calendar date. Storing it as midnight UTC would invent a
    # precision we do not have and read as the day before, west of Greenwich.
    published_date: date | None = None
    deadline: UtcDatetime | None = None

    notice_stage: NoticeStage = NoticeStage.OTHER
    notice_subtype: str | None = None
    contract_nature: ContractNature = ContractNature.UNKNOWN

    cpv_main: str | None = None
    cpv_additional: list[str] = Field(default_factory=list)

    estimated_value: Decimal | None = None
    currency: str | None = None

    documents_url: str | None = None
    languages: list[str] = Field(default_factory=list)
    multi_lot: bool = False

    first_seen_at: UtcDatetime = Field(default_factory=utc_now)
    last_seen_at: UtcDatetime = Field(default_factory=utc_now)

    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def id(self) -> str:
        return make_tender_id(self.source, self.source_id)

    @property
    def screening_text(self) -> str:
        """Title and description, the text the screening stage reads."""
        if not self.description:
            return self.title
        return f"{self.title}\n\n{self.description}"

    @property
    def content_hash(self) -> str:
        """Fingerprint of the title, description and CPV a screening would judge."""
        return content_hash(self.title, self.description, self.cpv_main, self.cpv_additional)


class TenderChange(BaseModel):
    """One material difference between two versions of the same notice."""

    model_config = ConfigDict(from_attributes=True)

    tender_id: str
    field: str
    old_value: str | None = None
    new_value: str | None = None
    source_version: str | None = None
    detected_at: UtcDatetime = Field(default_factory=utc_now)


class RuleMatch(BaseModel):
    """One alias found in one field, with the text around it kept as evidence."""

    model_config = ConfigDict(from_attributes=True)

    rule_id: str
    signal: RuleSignal
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


class Assessment(BaseModel):
    """The model's contract. Everything it is allowed to tell us, and nothing more."""

    model_config = ConfigDict(from_attributes=True)

    domain_fit: AxisScore[Domain]
    service_fit: AxisScore[ServiceType]
    stage_fit: AxisScore[DecisionStage]
    negative_signals: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    short_reason: str = Field(max_length=240)


class ScreeningResult(BaseModel):
    """One screening of one tender. Append-only: results are superseded, never edited."""

    model_config = ConfigDict(from_attributes=True)

    tender_id: str
    score: int = Field(ge=1, le=5)
    band: Band
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_reasons: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)

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
    notice_stage: NoticeStage | None = None
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
