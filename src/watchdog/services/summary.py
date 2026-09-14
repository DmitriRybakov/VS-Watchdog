"""The detail page, composed once in one place.

The page shows a register, not a notice dump, so this module turns a stored row
into the fields the team's own workbook has, in the workbook's order, each one
carrying two things a template cannot work out for itself:

- **the team's word for it**, from ``core.vocabulary``, so TED's word for a thing
  never reaches a colleague and a second source cannot introduce a second name;
- **where the value actually came from** - the platform's API, a document, an
  assessment, or a colleague - because a generated value displayed as a source
  fact is the failure this tool exists to avoid.

Empty fields are built here too, and deliberately. An absent field is not nothing:
it tells a colleague that the question was asked and the answer is not in what we
hold, which is a different statement from the field never having existed.

Two things it refuses to do:

- **It never pairs a label with a value that nothing associates.** TED sends an
  award criterion's names and its numbers as separate arrays and states no
  ordering between them. Equal lengths do not establish equal order, and this is
  true on a single-lot notice as well: knowing the criteria belong to that one lot
  says nothing about which percentage belongs to which description. So the parts
  are shown unpaired, with a sentence saying why. See docs/decisions/0008.
- **It never lets a rule that changed nothing look like one that did.** A matched
  exclusion that did not archive the notice is reported as exactly that.

No network call and no model call happens here. Rendering a page is reading.
"""

from __future__ import annotations

from dataclasses import dataclass

from watchdog.core.codelists import (
    award_criterion_type_label,
    criterion_number,
    dps_usage_label,
    framework_agreement_label,
    language_names,
    number_kind_label,
    selection_criterion_family,
    selection_criterion_label,
)
from watchdog.core.countries import country_name, country_names
from watchdog.core.enums import Domain, RuleSignal, RulesRoute, RuleStrength
from watchdog.core.models import RegisterRow, RuleMatch, Tender, TenderDetail
from watchdog.core.vocabulary import (
    FIELDS,
    GROUP_LABELS,
    GROUP_NOTES,
    FieldSpec,
    Group,
    Provenance,
    provenance_label,
    provenance_short,
)

# The three absences, in the words the templating layer already uses. Kept as
# keys rather than sentences so there is still exactly one copy of each phrase.
ABSENT_SOURCE = "source"
ABSENT_RETRIEVAL = "retrieval"
ABSENT_ASSESSMENT = "assessment"

# Selection-criterion codes that answer one of the register's own questions. The
# presence of a code in the notice's list is a fact on its own and does not
# depend on which description it was sent beside; see the module docstring.
_INDEMNITY_CODES = frozenset({"slc-stand-ins"})
_TEAM_CODES = frozenset({"slc-abil-educ", "slc-abil-tech", "slc-abil-staff-yrly-avg-mp"})


@dataclass(frozen=True)
class Value:
    """One thing shown against a field, with anything it needs qualifying by.

    Three different kinds of extra text hang off a value, and the page treats them
    differently because a colleague needs them in different places:

    - ``qualifier`` is part of the fact and is shown beside it. "Bids due" against
      a date is not a comment on the date, it is what the date is.
    - ``note`` explains what the value means to somebody reading it. It sits in the
      hover mark, because twenty notices' worth of it on screen is prose.
    - ``provenance_note`` says where this particular value was read from - a TED
      field name, a code, a section reference. It belongs in Technical details,
      where whoever debugs a mapping will look, and nowhere near the value.
    """

    text: str
    qualifier: str | None = None
    note: str | None = None
    provenance_note: str | None = None
    # An http(s) link, already checked by the template's ``safe_url`` filter.
    href: str | None = None
    # A BCP 47-ish code for a ``lang`` attribute, when the text is not English.
    language: str | None = None


@dataclass(frozen=True)
class FieldView:
    """One register field as the page shows it, empty ones included."""

    key: str
    label: str
    group: Group
    provenance: Provenance
    origin: str
    # The same origin as one word, for the marker that sits beside the label.
    origin_short: str
    note: str | None
    values: tuple[Value, ...]
    # Which kind of nothing this is when there are no values.
    absent: str
    prominent: bool
    # A sentence that has to be read before the values are acted on. Used where
    # the data is real but its association with a label is not established.
    caution: str | None = None

    @property
    def filled(self) -> bool:
        return bool(self.values)

    @property
    def help(self) -> str:
        """Everything the hover mark beside this label says, in one string."""
        parts = [self.note] if self.note else []
        parts.append(f"Where this comes from: {self.origin.lower()}.")
        return " ".join(parts)


@dataclass(frozen=True)
class GroupView:
    """One half of the register: what the documents say, or what Entr concluded."""

    key: str
    label: str
    note: str
    prominent: tuple[FieldView, ...]
    more: tuple[FieldView, ...]

    @property
    def fields(self) -> tuple[FieldView, ...]:
        return self.prominent + self.more


@dataclass(frozen=True)
class RuleGroup:
    """Matched rules of one kind, with what those matches actually did."""

    key: str
    heading: str
    # What this group of matches changed. Never "this matched", always the effect.
    outcome: str
    matches: tuple[RuleMatch, ...]


@dataclass(frozen=True)
class ProvenanceLine:
    """Where one displayed value was read from. Technical details, never the grid."""

    field: str
    value: str
    read_from: str


@dataclass(frozen=True)
class NoticeSummary:
    """Everything the detail page renders, worked out once."""

    row: RegisterRow
    detail: TenderDetail | None
    groups: tuple[GroupView, ...]
    rule_groups: tuple[RuleGroup, ...]

    @property
    def tender(self) -> Tender:
        return self.row.tender

    @property
    def provenance_trail(self) -> tuple[ProvenanceLine, ...]:
        """Every value that recorded which field or code it was read from."""
        return tuple(
            ProvenanceLine(field=view.label, value=value.text, read_from=value.provenance_note)
            for group in self.groups
            for view in group.fields
            for value in view.values
            if value.provenance_note
        )


def summarise(row: RegisterRow, *, detail: TenderDetail | None = None) -> NoticeSummary:
    """One stored row as the page shows it. Reads only what it is given."""
    views = [_field(spec, row, detail) for spec in FIELDS]
    groups = tuple(_group(group, views) for group in (Group.DOCUMENTS, Group.ANALYSIS))
    return NoticeSummary(
        row=row,
        detail=detail,
        groups=groups,
        rule_groups=rule_groups(row),
    )


# --------------------------------------------------------------- rule evidence


def rule_groups(row: RegisterRow) -> tuple[RuleGroup, ...]:
    """The matched rules split by what each match established, with the effect named.

    An exclusion listed beside a domain rule in the same style reads as evidence
    for the score, so a notice appears relevant partly because it is not social
    welfare. Worse, an exclusion that changed nothing - because a domain rule or a
    CPV code also matched - looks exactly like one that archived a notice. Both are
    fixed by separating the sections and saying what each group did.
    """
    screening = row.screening
    if screening is None:
        return ()

    matches = row.signals
    domain = [
        match
        for match in matches
        if match.signal is RuleSignal.DOMAIN and match.strength is not RuleStrength.SUPPORTING
    ]
    supporting = [
        match
        for match in matches
        if match.signal is RuleSignal.DOMAIN and match.strength is RuleStrength.SUPPORTING
    ]
    activity = [match for match in matches if match.signal is RuleSignal.ACTIVITY]
    stage = [match for match in matches if match.signal is RuleSignal.STAGE]
    exclusions = [match for match in matches if match.signal is RuleSignal.EXCLUSION]

    groups: list[RuleGroup] = []

    if domain:
        groups.append(
            RuleGroup(
                key="domain",
                heading="These established the subject matter",
                outcome=(
                    "The notice is recorded as being about "
                    f"{_domain_words(screening.rules.domains_hit)}. That is what stops an "
                    "exclusion archiving it, and it is what the register orders on."
                ),
                matches=tuple(domain),
            )
        )

    if activity:
        groups.append(
            RuleGroup(
                key="activity",
                heading="These established the kind of work",
                outcome=(
                    "The notice mentions work of a kind we do. This is evidence towards the "
                    "grade; on its own it establishes no subject matter."
                ),
                matches=tuple(activity),
            )
        )

    if stage:
        groups.append(
            RuleGroup(
                key="stage",
                heading="These pointed at how far the project has got",
                outcome=(
                    "Evidence about the project's stage. It does not establish subject "
                    "matter and it does not change the route."
                ),
                matches=tuple(stage),
            )
        )

    if supporting:
        groups.append(
            RuleGroup(
                key="supporting",
                heading="These are supporting terms and established nothing",
                outcome=(
                    "Recorded because they were found, and deliberately given no weight: a "
                    "supporting term such as \u201cenergy\u201d or \u201ccable\u201d can never "
                    "establish a subject matter on its own."
                ),
                matches=tuple(supporting),
            )
        )

    if exclusions:
        groups.append(
            RuleGroup(
                key="exclusion",
                heading="These are exclusions, and they are not evidence of relevance",
                outcome=_exclusion_outcome(row),
                matches=tuple(exclusions),
            )
        )

    return tuple(groups)


def _exclusion_outcome(row: RegisterRow) -> str:
    """What the matched exclusions did to this notice. Usually nothing."""
    screening = row.screening
    assert screening is not None  # only called with a screening present

    if screening.rules.route is RulesRoute.ARCHIVE_CANDIDATE:
        return (
            "Nothing else spoke for this notice, so these exclusions routed it to the "
            "archive candidate list. It is still screened, still scored and still "
            "searchable - archive means low priority, never deleted."
        )

    reasons: list[str] = []
    if screening.rules.domains_hit:
        reasons.append("a subject-matter rule also matched")
    if screening.rules.cpv_match:
        reasons.append("a CPV code on the archive guard list also matched")

    because = " and ".join(reasons) if reasons else "the exclusion route did not apply"
    return (
        f"These fired and changed nothing: {because}, so the notice went to assessment "
        "exactly as it would have without them. They are listed because they were found, "
        "not because they count towards the score."
    )


def _domain_words(domains: list[Domain]) -> str:
    if not domains:
        return "a subject matter we cover"
    return ", ".join(domain.value.replace("_", " ") for domain in domains)


# ------------------------------------------------------------------- the fields


def _group(group: Group, views: list[FieldView]) -> GroupView:
    members = [view for view in views if view.group is group]
    return GroupView(
        key=group.value,
        label=GROUP_LABELS[group],
        note=GROUP_NOTES[group],
        prominent=tuple(view for view in members if view.prominent),
        more=tuple(view for view in members if not view.prominent),
    )


def _field(spec: FieldSpec, row: RegisterRow, detail: TenderDetail | None) -> FieldView:
    builder = _BUILDERS[spec.key]
    built = builder(row, detail)
    return FieldView(
        key=spec.key,
        label=spec.label,
        group=spec.group,
        provenance=built.provenance,
        origin=provenance_label(built.provenance),
        origin_short=provenance_short(built.provenance),
        note=spec.note,
        values=built.values,
        absent=built.absent,
        prominent=spec.prominent,
        caution=built.caution,
    )


@dataclass(frozen=True)
class _Built:
    values: tuple[Value, ...] = ()
    provenance: Provenance = Provenance.SOURCE
    absent: str = ABSENT_SOURCE
    caution: str | None = None


def _extracted(detail: TenderDetail | None, key: str) -> _Built:
    """A value a document extraction filled, or the honest absence.

    An extraction that recorded where it read the value is a document extract; one
    that did not is the model's own reading, and the two are labelled differently.
    """
    field = detail.fields.get(key) if detail is not None else None
    if field is None or not (field.value or "").strip():
        return _Built(provenance=Provenance.DOCUMENT, absent=ABSENT_RETRIEVAL)

    origin = Provenance.DOCUMENT if field.source_ref else Provenance.ASSESSMENT
    read_from = f"Read from {field.source_ref}." if field.source_ref else None
    return _Built(
        values=(Value(text=field.value or "", provenance_note=read_from),), provenance=origin
    )


def _tender_name(row: RegisterRow, _: TenderDetail | None) -> _Built:
    tender = row.tender
    return _Built(
        values=(Value(text=tender.title, language=tender.title_language),),
    )


def _tender_name_native(row: RegisterRow, _: TenderDetail | None) -> _Built:
    tender = row.tender
    if not tender.title_native:
        return _Built()
    return _Built(
        values=(
            Value(
                text=tender.title_native,
                qualifier=tender.title_native_language or "language not stated",
                language=tender.title_native_language,
            ),
        )
    )


def _client(row: RegisterRow, _: TenderDetail | None) -> _Built:
    name = row.tender.buyer_name
    if not name:
        return _Built()
    return _Built(values=(Value(text=name),))


def _client_country(row: RegisterRow, _: TenderDetail | None) -> _Built:
    code = row.tender.buyer_country
    if not code:
        return _Built()
    # Named, never shown as a code: "FRA" beside "AGO" is two facts a colleague has
    # to look up before the notice means anything.
    return _Built(values=(Value(text=country_name(code) or code),))


def _project_location(row: RegisterRow, _: TenderDetail | None) -> _Built:
    tender = row.tender
    values: list[Value] = []

    if tender.place_of_performance_country:
        values.append(Value(text=", ".join(country_names(tender.place_of_performance_country))))

    if tender.performance_cities:
        values.append(Value(text=", ".join(tender.performance_cities), qualifier="town or city"))

    if tender.place_of_performance:
        values.append(
            Value(
                text=tender.place_of_performance,
                qualifier="region",
                provenance_note=(
                    "Region codes exactly as sent, unresolved; see docs/decisions/0003."
                ),
            )
        )

    return _Built(values=tuple(values), caution=_cross_border(row))


def _cross_border(row: RegisterRow) -> str:
    """Whether the work happens outside the client's own country.

    A separate sentence rather than an inference left to the reader: a French
    buyer procuring a waste roadmap for Angola is exactly the notice this register
    exists to find, and a page showing two country fields side by side does not
    say so.
    """
    tender = row.tender
    if not tender.buyer_country or not tender.place_of_performance_country:
        return (
            "Work outside the client's country: one of the two countries is missing, so this "
            "cannot be answered either way."
        )
    if tender.crosses_border_from_buyer:
        return (
            "Work outside the client's country: yes. The client sits in "
            f"{country_name(tender.buyer_country) or tender.buyer_country} and the work is in "
            f"{', '.join(country_names(tender.place_of_performance_country))}."
        )
    return "Work outside the client's country: no, the work is in the client's own country."


def _published_date(row: RegisterRow, _: TenderDetail | None) -> _Built:
    published = row.tender.published_date
    if published is None:
        return _Built()
    return _Built(
        values=(
            Value(
                text=published.isoformat(),
                note="A calendar date, which is all the platform states. No time of day.",
            ),
        )
    )


def _deadline_date(row: RegisterRow, _: TenderDetail | None) -> _Built:
    tender = row.tender
    kind = _DEADLINE_KINDS.get(tender.deadline_type.value, "Type of deadline not stated")
    read_from = f"Read from {tender.deadline_source}." if tender.deadline_source else None

    if tender.deadline is not None:
        # The converted date and the converted time, together. Splitting them
        # would put the buyer's local day beside a UTC clock, and around midnight
        # those are different days.
        stamp = tender.deadline.strftime("%Y-%m-%d %H:%M")
        return _Built(
            values=(Value(text=f"{stamp} UTC", qualifier=kind, provenance_note=read_from),),
        )

    if tender.deadline_date is not None:
        return _Built(
            values=(
                Value(
                    text=tender.deadline_date.isoformat(),
                    qualifier=kind,
                    note=(
                        "A date with no time of day; none has been invented, so this is not "
                        "converted to UTC."
                    ),
                    provenance_note=read_from,
                ),
            )
        )

    return _Built(
        caution=(
            "No deadline of any kind was sent with this notice. That is not a passed "
            "deadline, and it is not a reason to drop it from the working set."
        )
    )


_DEADLINE_KINDS = {
    "tender_submission": "Bids due",
    "participation_request": "Requests to participate due",
    "expression_of_interest": "Expressions of interest due",
    "unknown": "Type of deadline not stated",
}


def _public_platform(row: RegisterRow, _: TenderDetail | None) -> _Built:
    tender = row.tender
    return _Built(
        values=(
            Value(
                text=tender.source.value.upper(),
                provenance_note=f"Notice {tender.source_id}"
                + (f", version {tender.source_version}" if tender.source_version else ""),
                href=tender.source_url,
            ),
        )
    )


def _phase_of_tender(row: RegisterRow, _: TenderDetail | None) -> _Built:
    tender = row.tender
    subtype = f"Notice subtype {tender.notice_subtype}." if tender.notice_subtype else None
    return _Built(
        values=(
            Value(
                text=tender.notice_stage.value.replace("_", " ").capitalize(),
                provenance_note=subtype,
            ),
        )
    )


def _phase_of_project(row: RegisterRow, detail: TenderDetail | None) -> _Built:
    """Stated in the source, or read out of the text by a model. Never assumed.

    Empty until an assessment has run, and empty is the honest state: nothing in a
    search response says how far a buyer's own project has travelled.
    """
    from_detail = _extracted(detail, "phase_of_project")
    if from_detail.values:
        return from_detail

    screening = row.screening
    assessment = screening.assessment if screening is not None else None
    if assessment is None or not assessment.stage_fit.is_established:
        return _Built(provenance=Provenance.ASSESSMENT, absent=ABSENT_ASSESSMENT)

    axis = assessment.stage_fit
    return _Built(
        values=(
            Value(
                text=axis.label.value.replace("_", " ").capitalize(),
                note=(
                    "A model's reading of the notice text, not something the buyer stated. "
                    "The quotes it rests on are under the assessment below."
                ),
            ),
        ),
        provenance=Provenance.ASSESSMENT,
        absent=ABSENT_ASSESSMENT,
    )


def _duration(row: RegisterRow, _: TenderDetail | None) -> _Built:
    tender = row.tender
    values: list[Value] = []

    for item in tender.contract_durations:
        unit = (item.unit or "").strip().lower()
        text = f"{item.value} {unit}" if unit else f"{item.value} (unit not stated)"
        values.append(Value(text=text))

    for start in tender.contract_start_dates:
        values.append(Value(text=f"Starts {start.isoformat()}"))

    if tender.renewal_maximums:
        values.append(Value(text=f"Renewals: {', '.join(tender.renewal_maximums)} at most"))

    caution = _across_lots_caution(tender) if values else None
    return _Built(values=tuple(values), caution=caution)


def _budget(row: RegisterRow, _: TenderDetail | None) -> _Built:
    tender = row.tender
    values: list[Value] = []

    if tender.estimated_value is not None:
        note = None
        if tender.estimated_value_source == "estimated-value-lot":
            note = (
                "The notice states no value for the procedure as a whole; this is its "
                "single lot's value."
            )
        values.append(
            Value(
                text=f"{tender.estimated_value} {tender.currency or '(currency not stated)'}",
                note=note,
            )
        )

    if tender.lot_values and tender.estimated_value_source != "estimated-value-lot":
        currency = tender.lot_value_currency or "(currency not stated)"
        joined = ", ".join(str(value) for value in tender.lot_values)
        values.append(
            Value(
                text=f"Per lot: {joined} {currency}",
                note=(
                    f"{len(tender.lot_values)} value(s) across "
                    f"{tender.lot_count or 'an unstated number of'} lot(s), listed as given. "
                    "Not added up and not matched to particular lots."
                ),
            )
        )

    for code in tender.framework_agreements:
        label = framework_agreement_label(code)
        values.append(
            Value(text=label or code, provenance_note=None if label else "Code not in our list.")
        )

    for code in tender.dps_usages:
        label = dps_usage_label(code)
        values.append(
            Value(text=label or code, provenance_note=None if label else "Code not in our list.")
        )

    return _Built(values=tuple(values))


def _indemnity(row: RegisterRow, detail: TenderDetail | None) -> _Built:
    """Whether the notice lists professional indemnity among its qualification bars.

    Read from the presence of the code in the notice's list of selection criteria,
    which is a fact on its own: it does not depend on which description TED sent
    the code beside, and that association is not established. See correction D in
    docs/decisions/0008.
    """
    codes = {(item.type or "").strip().lower() for item in row.tender.selection_criteria}
    if codes & _INDEMNITY_CODES:
        return _Built(
            values=(
                Value(
                    text="Listed as a qualification requirement by the notice",
                    note=(
                        "The amount and the terms are in the tender documents, which "
                        "Watchdog does not read yet."
                    ),
                ),
            )
        )
    return _extracted(detail, "professional_indemnity_requirement")


def _team_composition(row: RegisterRow, detail: TenderDetail | None) -> _Built:
    codes = {(item.type or "").strip().lower() for item in row.tender.selection_criteria}
    named = sorted(codes & _TEAM_CODES)
    if named:
        return _Built(
            values=tuple(
                Value(text=selection_criterion_label(code) or code, provenance_note=code)
                for code in named
            )
        )
    return _extracted(detail, "team_composition_requirement")


def _submission_language(row: RegisterRow, _: TenderDetail | None) -> _Built:
    tender = row.tender
    published_in = ", ".join(language_names(tender.languages)) or "not stated"

    if not tender.submission_languages:
        return _Built(
            caution=f"The notice itself was published in: {published_in}. That is a different fact."
        )

    note = f"The notice itself was published in: {published_in}. That is a different fact."
    if tender.multi_lot:
        note = f"Mentioned across lots, and not necessarily every one for every lot. {note}"
    return _Built(
        values=(Value(text=", ".join(language_names(tender.submission_languages)), note=note),)
    )


def _experience(row: RegisterRow, _: TenderDetail | None) -> _Built:
    """The qualification bar: the requirement types in words, and the notice's own text.

    The two are shown as separate lists. TED sends ``selection-criterion-lot`` and
    ``selection-criterion-description-lot`` as parallel arrays with no stated
    ordering, and they disagree in length on 82% of the notices that carry them.
    """
    tender = row.tender
    values: list[Value] = []

    for item in tender.selection_criteria:
        code = (item.type or "").strip()
        if not code:
            continue
        plain = selection_criterion_label(code)
        family = selection_criterion_family(code)
        if plain:
            values.append(Value(text=plain, qualifier=family))
        elif family:
            values.append(
                Value(
                    text=family,
                    provenance_note=f"TED code {code}; its exact meaning is not in our list.",
                )
            )
        else:
            values.append(
                Value(
                    text=code,
                    provenance_note="A code we do not recognise, shown as it arrived.",
                )
            )

    descriptions = [
        (item.description or "").strip()
        for item in tender.selection_criteria
        if (item.description or "").strip()
    ]

    caution = None
    if descriptions:
        caution = (
            "The buyer's own wording is listed below. TED sends the requirement codes and "
            "their descriptions as two separate lists and states no ordering between them, "
            "so no description is shown against a code."
        )
        values.extend(
            Value(text=text, qualifier="buyer's own wording, unmatched") for text in descriptions
        )

    return _Built(values=tuple(values), caution=caution)


def _award_criteria(row: RegisterRow, _: TenderDetail | None) -> _Built:
    """How a bid would be judged, with the numbers kept apart from the names.

    A wrong weight against the right criterion name is a mistake a colleague acts
    on and nobody catches, so no number is shown against a criterion. TED sends
    them as parallel arrays and states no ordering, and equal lengths do not prove
    equal order - on a single-lot notice either.
    """
    tender = row.tender

    if tender.criteria_unpaired:
        return _Built(
            caution=(
                "TED's arrays for the criteria on this notice disagree in length, so their "
                "parts could not be read as one list at all. The criteria are on the notice "
                "itself."
            )
        )

    if not tender.award_criteria:
        return _Built()

    values: list[Value] = []
    for item in tender.award_criteria:
        text = (item.name or "").strip() or (item.description or "").strip()
        kind = award_criterion_type_label(item.type)
        if not text:
            continue
        values.append(Value(text=text, qualifier=kind))

    numbers: list[str] = []
    for item in tender.award_criteria:
        written = criterion_number(item.number, item.number_kind)
        if written is None:
            continue
        if number_kind_label(item.number_kind) is None:
            written = f"{written} (TED did not say what this number means)"
        numbers.append(written)

    caution = None
    if numbers:
        values.append(
            Value(
                text=", ".join(numbers),
                note="Every number the notice states, in the order it stated them.",
            )
        )
        caution = (
            "The numbers are listed separately from the criteria on purpose. TED sends the "
            "names and the numbers as two arrays and states no ordering between them, so "
            "which number belongs to which criterion is not in the data - on a single-lot "
            "notice either."
        )
    elif tender.multi_lot:
        caution = _across_lots_caution(tender)

    return _Built(values=tuple(values), caution=caution)


def _documents_link(row: RegisterRow, _: TenderDetail | None) -> _Built:
    urls = row.tender.document_urls
    if not urls:
        return _Built()
    return _Built(
        values=tuple(
            Value(
                text=f"Procurement documents {index}" if len(urls) > 1 else "Procurement documents",
                href=url,
            )
            for index, url in enumerate(urls, start=1)
        )
    )


def _scope_technical(_: RegisterRow, detail: TenderDetail | None) -> _Built:
    return _assessment_absence(_extracted(detail, "scope_of_work_technical"))


def _scope_commercial(_: RegisterRow, detail: TenderDetail | None) -> _Built:
    return _assessment_absence(_extracted(detail, "scope_of_work_commercial"))


def _documents_required(_: RegisterRow, detail: TenderDetail | None) -> _Built:
    return _assessment_absence(_extracted(detail, "documents_likely_required"))


def _local_content(_: RegisterRow, detail: TenderDetail | None) -> _Built:
    return _extracted(detail, "local_content_requirement")


def _site_visit(_: RegisterRow, detail: TenderDetail | None) -> _Built:
    return _extracted(detail, "site_visit_requirement")


def _assessment_absence(built: _Built) -> _Built:
    """An empty analysis field says "no assessment", not "the notice did not say"."""
    if built.values:
        return built
    return _Built(provenance=Provenance.ASSESSMENT, absent=ABSENT_ASSESSMENT)


def _across_lots_caution(tender: Tender) -> str | None:
    if not tender.multi_lot:
        return None
    return (
        f"Stated somewhere on a notice with {tender.lot_count or 'several'} lots. TED does "
        "not say which lot each value belongs to, so none is attached to one."
    )


# One builder per field key. A key with no builder is a mistake that fails at
# import time rather than rendering as a blank.
_BUILDERS = {
    "tender_name": _tender_name,
    "tender_name_native": _tender_name_native,
    "client": _client,
    "client_country": _client_country,
    "project_location": _project_location,
    "published_date": _published_date,
    "deadline_date": _deadline_date,
    "public_platform": _public_platform,
    "phase_of_tender": _phase_of_tender,
    "phase_of_project": _phase_of_project,
    "scope_of_work_technical": _scope_technical,
    "scope_of_work_commercial": _scope_commercial,
    "duration": _duration,
    "budget_and_commercial_model": _budget,
    "professional_indemnity_requirement": _indemnity,
    "local_content_requirement": _local_content,
    "site_visit_requirement": _site_visit,
    "submission_language": _submission_language,
    "team_composition_requirement": _team_composition,
    "experience_qualification_requirement": _experience,
    "evaluation_and_award_criteria": _award_criteria,
    "procurement_documents_link": _documents_link,
    "documents_likely_required": _documents_required,
}

_MISSING = sorted({spec.key for spec in FIELDS} - set(_BUILDERS))
if _MISSING:  # pragma: no cover - a coding error, caught at import
    raise RuntimeError(f"no detail builder for register field(s): {', '.join(_MISSING)}")
