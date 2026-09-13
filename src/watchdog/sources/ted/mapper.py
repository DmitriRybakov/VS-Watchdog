"""Mapping one TED notice onto the canonical Tender. Pure: no network, no clock.

The five things here that are not obvious, all of them read off live responses:

**Two multilingual shapes.** ``notice-title`` and ``description-proc`` are
``{lang: "text"}``. ``title-lot``, ``description-lot`` and ``buyer-name`` are
``{lang: ["text", ...]}``, a list even when there is one entry. One accessor
copes with both.

**Two different titles.** ``notice-title`` is composed by TED as
"<country> - <CPV label> - <buyer's title>" and translated into all 24 EU
languages. Its CPV label is the code we filtered on, restated, so screening it
would make an activity rule fire on every notice. ``title-proc`` is what the
buyer wrote, in the buyer's own language, and was present on 750 of 750 notices
sampled. Screening reads ``title-proc``; the composed title is for display only.

**Nothing is ever attributed to a lot.** The search API flattens every lot-scoped
field into one array and states no ordering, so which lot a value belongs to
cannot be established from a search response at all. Notice 458521-2026 has two
lots - ``LOT-0001`` and ``LOT-0003``, not even contiguous - and seven award
criteria, four belonging to the first lot and three to the second, with nothing
marking the boundary. Equal lengths would not help either: they do not prove
equal ordering. So lot-scoped values are held as the distinct set across lots and
labelled that way, and lot-level attribution, if it is ever needed, comes from the
notice XML. See docs/decisions/0008.

**The criterion arrays are paired with each other only when they agree.** An
award criterion's type, name, number and number kind arrive as parallel arrays.
They disagree in length on 2.5% of notices carrying them (395741-2026: 113 types
against 157 numbers), so they are read by position only when every array that is
present has the same length, exactly as the deadline date and time arrays are.
Otherwise nothing is paired and ``criteria_unpaired`` says so.

**Dates carry an offset without a time.** ``publication-date`` is
``2026-06-15+02:00``: a calendar date, not a moment, and not parseable by
``date.fromisoformat``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from watchdog.core.cpv import normalise_cpv, normalise_cpv_list
from watchdog.core.enums import ContractNature, DeadlineType, SourcePlatform
from watchdog.core.logging import get_logger
from watchdog.core.models import (
    AwardCriterion,
    ContractDuration,
    SelectionCriterion,
    Tender,
    TextBlock,
)
from watchdog.sources.errors import MappingError
from watchdog.sources.ted.vocabulary import CPV_SCHEME, contract_nature_for, stage_for

log = get_logger(__name__)

# Exactly what we ask TED for. Every name was checked against the live endpoint;
# one unrecognised name fails the whole request, so `watchdog config validate`
# checks this list. Order is for reading, not for meaning.
#
# The length of this list is not free: TED prices a request as fields per page and
# refuses one over 10,000, so adding a name costs page size. See
# docs/TED_API_CONTRACT.md and `watchdog.sources.ted.config.fields_per_page`.
REQUESTED_FIELDS: tuple[str, ...] = (
    # identity and version
    "publication-number",
    "notice-identifier",
    "change-notice-version-identifier",
    # titles and descriptions
    "notice-title",
    "title-proc",
    "title-lot",
    "description-proc",
    "description-lot",
    # buyer and place
    "buyer-name",
    "buyer-country",
    "main-activity",
    "place-of-performance",
    "place-of-performance-country-proc",
    "place-of-performance-country-lot",
    "place-of-performance-city-proc",
    # dates
    "publication-date",
    "deadline-receipt-tender-date-lot",
    "deadline-receipt-tender-time-lot",
    "deadline-receipt-request-date-lot",
    "deadline-receipt-request-time-lot",
    "deadline-receipt-expressions-date-lot",
    "deadline-receipt-expressions-time-lot",
    "deadline-receipt-request",
    # classification
    "classification-cpv",
    "main-classification-proc",
    "main-classification-type-proc",
    "additional-classification-proc",
    "contract-nature",
    "contract-nature-main-proc",
    # stage
    "notice-type",
    "form-type",
    "notice-subtype",
    "procedure-type",
    # value
    "estimated-value-proc",
    "estimated-value-cur-proc",
    "estimated-value-lot",
    "estimated-value-cur-lot",
    # lots: the identifiers are the only authoritative lot count
    "identifier-lot",
    # how to bid, and under what terms
    "submission-language",
    "submission-url-lot",
    "framework-agreement-lot",
    "dps-usage-lot",
    "contract-duration-period-lot",
    "contract-duration-start-date-lot",
    "renewal-maximum-lot",
    # how a bid is judged, and who may bid at all
    "award-criterion-type-lot",
    "award-criterion-name-lot",
    "award-criterion-description-lot",
    "award-criterion-number-lot",
    "award-criterion-number-weight-lot",
    "selection-criterion-lot",
    "selection-criterion-description-lot",
    # links
    "document-url-lot",
    "links",
    "official-language",
)

# Read in this order. Each entry establishes what the deadline *means*, which is a
# different fact from when it is.
DEADLINE_CHAIN: tuple[tuple[str, str, DeadlineType], ...] = (
    (
        "deadline-receipt-tender-date-lot",
        "deadline-receipt-tender-time-lot",
        DeadlineType.TENDER_SUBMISSION,
    ),
    (
        "deadline-receipt-request-date-lot",
        "deadline-receipt-request-time-lot",
        DeadlineType.PARTICIPATION_REQUEST,
    ),
    (
        "deadline-receipt-expressions-date-lot",
        "deadline-receipt-expressions-time-lot",
        DeadlineType.EXPRESSION_OF_INTEREST,
    ),
)

# A union of tender submission, participation request and expression of interest.
# It agrees with the specific fields wherever both exist, which makes it a good
# last resort and a poor first choice: it does not say which of the three it is.
DEADLINE_UNION_FIELD = "deadline-receipt-request"

# Never read. `deadline` and `deadline-date-lot` are a different deadline
# entirely: on 597197-2026 they say 2026-08-18 while the tender deadline is
# 2026-09-07. A deadline three weeks early is worse than no deadline.
REJECTED_DEADLINE_FIELDS: tuple[str, ...] = ("deadline", "deadline-date-lot")

# Lot-level arrays consulted only when `identifier-lot` is missing, which was the
# case on 67 of 2,158 notices. Their lengths say how many lots there are; their
# contents are never zipped together with anything.
LOT_ARRAY_FIELDS: tuple[str, ...] = (
    "title-lot",
    "description-lot",
    "estimated-value-lot",
    "deadline-receipt-tender-date-lot",
    "document-url-lot",
)

# One award criterion's parts, in the order they are read. Paired by position
# only when every part that is present has the same length.
AWARD_CRITERION_FIELDS: tuple[tuple[str, str], ...] = (
    ("type", "award-criterion-type-lot"),
    ("name", "award-criterion-name-lot"),
    ("description", "award-criterion-description-lot"),
    ("number", "award-criterion-number-lot"),
    ("number_kind", "award-criterion-number-weight-lot"),
)

SELECTION_CRITERION_FIELDS: tuple[tuple[str, str], ...] = (
    ("type", "selection-criterion-lot"),
    ("description", "selection-criterion-description-lot"),
)

ENGLISH = "eng"

# "2026-06-15+02:00" and "2026-08-18Z": a date with an offset and no time.
_DATE_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})(?P<offset>Z|[+-]\d{2}:?\d{2})?$")
# "10:15:00+02:00", "21:59:59Z".
_TIME_RE = re.compile(r"^(?P<time>\d{2}:\d{2}:\d{2}(?:\.\d+)?)(?P<offset>Z|[+-]\d{2}:?\d{2})?$")


@dataclass(frozen=True)
class _Deadline:
    """What we could establish about a deadline, and where each part came from."""

    moment: datetime | None = None
    day: date | None = None
    source: str | None = None
    kind: DeadlineType = DeadlineType.UNKNOWN


def map_notice(
    payload: dict[str, Any],
    *,
    source: SourcePlatform = SourcePlatform.TED,
    first_seen_at: datetime | None = None,
    last_seen_at: datetime | None = None,
) -> Tender:
    """Turn one TED notice into a Tender, or raise MappingError naming the notice."""
    source_id = _text(_first(payload.get("publication-number")))
    if not source_id:
        raise MappingError("the notice has no publication-number, so it has no identity")

    try:
        return _map(payload, source_id, source, first_seen_at, last_seen_at)
    except MappingError:
        raise
    except Exception as exc:
        raise MappingError(f"could not map the notice: {exc}", source_id=source_id) from exc


def _map(
    payload: dict[str, Any],
    source_id: str,
    source: SourcePlatform,
    first_seen_at: datetime | None,
    last_seen_at: datetime | None,
) -> Tender:
    official_languages = [
        code
        for code in (_text(item) for item in _as_list(payload.get("official-language")))
        if code
    ]

    native_titles = _language_map(payload.get("title-proc"))
    native_language = _preferred_language(native_titles, official_languages)
    title_native = _first(native_titles.get(native_language)) if native_language else None

    composed = _language_map(payload.get("notice-title"))
    composed_language = ENGLISH if ENGLISH in composed else _alphabetically_first(composed)
    title = _first(composed.get(composed_language)) if composed_language else None

    if not title:
        # The display title is missing; the buyer's own title is a better label
        # than nothing, and losing the notice entirely would be worse than both.
        title = title_native
        composed_language = native_language

    if not title:
        raise MappingError(
            "the notice has neither notice-title nor title-proc", source_id=source_id
        )

    if not title_native:
        log.warning("ted_notice_without_title_proc", source_id=source_id)

    descriptions = _language_map(payload.get("description-proc"))
    description_language = _preferred_language(descriptions, official_languages, native_language)
    description = _first(descriptions.get(description_language)) if description_language else None
    if not description:
        description = _first(_flatten(_language_map(payload.get("description-lot"))))

    buyers = _language_map(payload.get("buyer-name"))
    buyer_language = _preferred_language(buyers, official_languages, native_language)
    buyer_name = _first(buyers.get(buyer_language)) if buyer_language else None

    deadline = _map_deadline(payload)
    value = _map_value(payload, source_id)
    natures = _map_contract_natures(payload)
    lot_ids = _codes(payload.get("identifier-lot"), upper=False)
    award_criteria, selection_criteria, unpaired = _map_criteria(payload, source_id)

    return Tender(
        source=source,
        source_id=source_id,
        source_version=_text(_first(payload.get("change-notice-version-identifier"))),
        source_url=_notice_url(payload),
        title=title,
        title_language=composed_language,
        title_native=title_native,
        title_native_language=native_language,
        description=description,
        buyer_name=buyer_name,
        buyer_country=_text(_first(payload.get("buyer-country"))),
        main_activity=_text(_first(payload.get("main-activity"))),
        place_of_performance=_place_of_performance(payload),
        place_of_performance_country=_place_of_performance_country(payload),
        performance_cities=_distinct_text(payload.get("place-of-performance-city-proc")),
        published_date=_map_published_date(payload),
        deadline=deadline.moment,
        deadline_date=deadline.day,
        deadline_source=deadline.source,
        deadline_type=deadline.kind,
        notice_stage=stage_for(
            _text(_first(payload.get("form-type"))),
            _text(_first(payload.get("notice-type"))),
        ),
        notice_subtype=_text(_first(payload.get("notice-subtype"))),
        procedure_type=_text(_first(payload.get("procedure-type"))),
        contract_nature=_map_main_nature(payload, natures),
        contract_natures=natures,
        cpv_main=_map_cpv_main(payload),
        cpv_additional=normalise_cpv_list(_as_list(payload.get("additional-classification-proc"))),
        cpv_all=_map_cpv_all(payload),
        estimated_value=value.amount,
        currency=value.currency,
        estimated_value_source=value.source,
        lot_values=list(value.lot_values),
        lot_value_currency=value.lot_currency,
        document_urls=_distinct_text(payload.get("document-url-lot")),
        languages=official_languages,
        lot_ids=lot_ids,
        multi_lot=_is_multi_lot(payload, lot_ids),
        submission_languages=_codes(payload.get("submission-language")),
        submission_urls=_distinct_text(payload.get("submission-url-lot")),
        framework_agreements=_distinct_text(payload.get("framework-agreement-lot")),
        dps_usages=_distinct_text(payload.get("dps-usage-lot")),
        contract_durations=_map_durations(payload),
        contract_start_dates=_map_start_dates(payload),
        renewal_maximums=_distinct_text(payload.get("renewal-maximum-lot")),
        award_criteria=award_criteria,
        selection_criteria=selection_criteria,
        criteria_unpaired=unpaired,
        screening_blocks=_screening_blocks(payload),
        # Everything the source sent, so a mapping can be re-checked or redone
        # later: lot arrays, every language variant, the fields we chose not to read.
        raw=dict(payload),
        **_seen_at(first_seen_at, last_seen_at),
    )


def _seen_at(first: datetime | None, last: datetime | None) -> dict[str, Any]:
    """Only pass timestamps the caller gave, so the model's defaults still apply."""
    seen: dict[str, Any] = {}
    if first is not None:
        seen["first_seen_at"] = first
    if last is not None:
        seen["last_seen_at"] = last
    return seen


# ------------------------------------------------------------------ screening


def _screening_blocks(payload: dict[str, Any]) -> list[TextBlock]:
    """Every original text variant, labelled, in a deterministic order.

    All languages of the buyer's own title and description are included: a Flemish
    buyer's Dutch and French titles are two chances to match, and discarding one
    costs recall for nothing. Identical lot descriptions are collapsed, because
    the same paragraph repeated six times is one piece of evidence.

    The composed ``notice-title`` is never included; see docs/decisions/0002.
    """
    blocks: list[TextBlock] = []

    for field in ("title-proc", "description-proc", "description-lot"):
        by_language = _language_map(payload.get(field))
        for language in sorted(by_language):
            seen: set[str] = set()
            for text in by_language[language]:
                cleaned = text.strip()
                if not cleaned or cleaned in seen:
                    continue
                seen.add(cleaned)
                blocks.append(TextBlock(field=field, language=language, text=cleaned))

    return blocks


# ----------------------------------------------------------------- deadlines


def _map_deadline(payload: dict[str, Any]) -> _Deadline:
    """The deadline, what it means, and which field it came from.

    The date and time arrays of one deadline field are paired by position, but
    only when they are the same length. Across 1250 sampled notices they always
    were, and a date field never arrived without its time field - so the
    date-only branch below is a safeguard, not an observed case. If the lengths
    ever disagree, pairing would invent a time, so the time is dropped instead.

    Where lots have different deadlines the earliest wins: this tool's job is not
    to miss things.
    """
    for date_field, time_field, kind in DEADLINE_CHAIN:
        days = _parse_dates(_as_list(payload.get(date_field)))
        if not days:
            continue

        times = _parse_times(_as_list(payload.get(time_field)))

        moments: list[datetime] = []
        if len(times) == len(days):
            moments = [_combine(day, moment) for day, moment in zip(days, times, strict=True)]
        elif len(_unique(times)) == 1:
            moments = [_combine(day, times[0]) for day in days]

        if moments:
            earliest_moment = min(moments)
            return _Deadline(
                moment=_utc(earliest_moment),
                # The calendar date the buyer stated, not its UTC equivalent.
                day=earliest_moment.date(),
                source=f"{date_field}+{time_field}",
                kind=kind,
            )

        return _Deadline(
            moment=None,
            day=min(days).date(),
            source=date_field,
            kind=kind,
        )

    moments = _parse_moments(_as_list(payload.get(DEADLINE_UNION_FIELD)))
    if moments:
        earliest = min(moments)
        return _Deadline(
            moment=_utc(earliest),
            day=earliest.date(),
            source=DEADLINE_UNION_FIELD,
            # The union field does not distinguish the three kinds it merges.
            kind=DeadlineType.UNKNOWN,
        )

    return _Deadline()


def _combine(day: datetime, moment: time) -> datetime:
    """A date and a time of day into one moment, using the time's own offset."""
    tz = moment.tzinfo or day.tzinfo
    return datetime.combine(day.date(), moment.replace(tzinfo=None), tzinfo=tz)


def _utc(value: datetime | None) -> datetime | None:
    """UTC, but only when the source told us the offset. Never assume one."""
    if value is None or value.tzinfo is None:
        return None
    return value.astimezone(UTC)


# ---------------------------------------------------------------------- dates


def _map_published_date(payload: dict[str, Any]) -> date | None:
    """TED's own calendar date, so a cross-check on the website shows the same day."""
    parsed = _parse_date(_text(_first(payload.get("publication-date"))))
    return parsed.date() if parsed else None


def _parse_date(value: str | None) -> datetime | None:
    """``2026-06-15+02:00`` into an offset-aware midnight. None if it is not a date."""
    if not value:
        return None

    match = _DATE_RE.match(value.strip())
    if match is None:
        return None

    try:
        day = date.fromisoformat(match.group("date"))
    except ValueError:
        return None

    return datetime.combine(day, time.min, tzinfo=_offset(match.group("offset")))


def _parse_time(value: str | None) -> time | None:
    if not value:
        return None

    match = _TIME_RE.match(value.strip())
    if match is None:
        return None

    try:
        parsed = time.fromisoformat(match.group("time"))
    except ValueError:
        return None

    return parsed.replace(tzinfo=_offset(match.group("offset")))


def _parse_moment(value: str | None) -> datetime | None:
    """A full ``2026-09-28T10:15:00+02:00`` timestamp."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def _offset(raw: str | None) -> timezone | None:
    if not raw:
        return None
    if raw == "Z":
        return UTC

    sign = -1 if raw[0] == "-" else 1
    digits = raw[1:].replace(":", "")
    hours, minutes = int(digits[:2]), int(digits[2:4])
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def _parse_dates(values: Sequence[Any]) -> list[datetime]:
    return [parsed for parsed in (_parse_date(_text(v)) for v in values) if parsed]


def _parse_times(values: Sequence[Any]) -> list[time]:
    return [parsed for parsed in (_parse_time(_text(v)) for v in values) if parsed]


def _parse_moments(values: Sequence[Any]) -> list[datetime]:
    return [
        parsed
        for parsed in (_parse_moment(_text(v)) for v in values)
        if parsed and parsed.tzinfo is not None
    ]


# ----------------------------------------------------------- classification


def _map_cpv_main(payload: dict[str, Any]) -> str | None:
    """The procedure's main code, but only if it is a CPV code at all.

    TED states the scheme separately. A code from a scheme we do not understand is
    not stored as a CPV code.
    """
    scheme = _text(_first(payload.get("main-classification-type-proc")))
    if scheme is not None and scheme.strip().lower() != CPV_SCHEME:
        return None
    return normalise_cpv(_first(payload.get("main-classification-proc")))


def _map_cpv_all(payload: dict[str, Any]) -> list[str]:
    """Every code on the notice, procedure and lot level, deduplicated.

    ``classification-cpv`` is the union TED itself matches against, and the code
    that makes a notice interesting is often only on a lot while the
    procedure-level code is something generic such as 71000000.
    """
    codes: list[Any] = list(_as_list(payload.get("classification-cpv")))
    codes.extend(_as_list(payload.get("main-classification-proc")))
    codes.extend(_as_list(payload.get("additional-classification-proc")))
    return normalise_cpv_list(codes)


def _map_contract_natures(payload: dict[str, Any]) -> list[ContractNature]:
    """Every nature the notice mentions, deduplicated, order preserved.

    TED's own ``contract-nature=services`` filter matches if services appears
    anywhere in this list, so a works contract with a services component is kept.
    Every filter of ours reads this list for the same reason.
    """
    natures: list[ContractNature] = []
    for value in _as_list(payload.get("contract-nature")):
        nature = contract_nature_for(_text(value))
        if nature is not None and nature not in natures:
            natures.append(nature)
    return natures


def _map_main_nature(payload: dict[str, Any], natures: Sequence[ContractNature]) -> ContractNature:
    """The procedure-level nature as TED states it, not a summary of the list."""
    main = contract_nature_for(_text(_first(payload.get("contract-nature-main-proc"))))
    if main is not None:
        return main
    if len(natures) == 1:
        return natures[0]
    if len(natures) > 1:
        return ContractNature.MIXED
    return ContractNature.UNKNOWN


# ----------------------------------------------------------------- value, place


@dataclass(frozen=True)
class _Value:
    """What we could establish about money, and which field each part came from."""

    amount: Decimal | None = None
    currency: str | None = None
    source: str | None = None
    lot_values: tuple[Decimal, ...] = ()
    lot_currency: str | None = None


def _map_value(payload: dict[str, Any], source_id: str) -> _Value:
    """The estimate, its currency, and the field it was read from.

    The procedure-level estimate is preferred. When there is none, a notice with
    exactly one lot still states a value we can report as the notice's own: there
    is only one lot, so there is nothing to attribute wrongly. 55 notices over
    1 July to 11 September showed "Not stated" for exactly this reason.

    With several lots the values are kept as a list and nothing is promoted.
    They are never summed: 532622-2026 carries four values for five lots, so a
    total would be confidently short, and they are never paired with a lot.

    A value with no currency is not stored, because a bare number reads as euros
    to whoever sees it next. ``estimated-value-cur-lot`` is a single entry against
    six values on 598884-2026, so it is used only when it names one currency.
    """
    lot_values = _decimals(payload.get("estimated-value-lot"), source_id)
    lot_currencies = _codes(payload.get("estimated-value-cur-lot"))
    lot_currency = lot_currencies[0] if len(lot_currencies) == 1 else None

    procedure_currency = _text(_first(payload.get("estimated-value-cur-proc")))
    raw_value = _text(_first(payload.get("estimated-value-proc")))

    if raw_value is not None:
        try:
            amount = Decimal(raw_value)
        except (InvalidOperation, ValueError):
            log.warning("ted_unparseable_estimated_value", source_id=source_id)
        else:
            if procedure_currency is None:
                log.warning("ted_value_without_currency", source_id=source_id)
            else:
                return _Value(
                    amount=amount,
                    currency=procedure_currency,
                    source="estimated-value-proc",
                    lot_values=lot_values,
                    lot_currency=lot_currency,
                )

    currency = procedure_currency or lot_currency

    if len(lot_values) == 1 and currency is not None:
        return _Value(
            amount=lot_values[0],
            currency=currency,
            source="estimated-value-lot",
            lot_values=lot_values,
            lot_currency=lot_currency,
        )

    return _Value(lot_values=lot_values, lot_currency=lot_currency)


def _decimals(value: Any, source_id: str) -> tuple[Decimal, ...]:
    """Every parseable number in a list, in order, duplicates kept.

    Duplicates are real: four lots priced at 7,000,000 each on 598884-2026 are
    four facts, and collapsing them would make a six-lot notice look like a
    three-lot one.
    """
    amounts: list[Decimal] = []
    for item in _as_list(value):
        text = _text(item)
        if text is None:
            continue
        try:
            amounts.append(Decimal(text))
        except (InvalidOperation, ValueError):
            log.warning("ted_unparseable_lot_value", source_id=source_id)
    return tuple(amounts)


def _place_of_performance(payload: dict[str, Any]) -> str | None:
    """The codes as given: NUTS regions and ISO-3 countries in one list.

    Not resolved to place names; see docs/decisions/0003.
    """
    codes = _unique(
        [
            code
            for code in (_text(item) for item in _as_list(payload.get("place-of-performance")))
            if code
        ]
    )
    return ", ".join(codes) if codes else None


def _place_of_performance_country(payload: dict[str, Any]) -> list[str]:
    """Where the work happens, at country level, deduplicated.

    The procedure-level field is the one to read: it was present on every fixture
    while the lot-level one was not, and it repeats once per lot so deduplication
    is not optional. Several distinct countries are all kept - the notice really
    does span them - and ``Tender.multi_country`` says so.

    A code we cannot name is still stored. This is a fact from the source, not a
    configured value, and the display layer falls back to showing the code.
    """
    codes: list[str] = []
    for field in ("place-of-performance-country-proc", "place-of-performance-country-lot"):
        for item in _as_list(payload.get(field)):
            code = _text(item)
            if code is None:
                continue
            code = code.strip().upper()
            if len(code) == 3 and code.isalpha() and code not in codes:
                codes.append(code)
    return codes


def _notice_url(payload: dict[str, Any]) -> str | None:
    """The English HTML notice page. Language keys in ``links`` are UPPER CASE."""
    links = payload.get("links")
    if not isinstance(links, dict):
        return None

    for kind in ("html", "htmlDirect", "pdf"):
        by_language = links.get(kind)
        if not isinstance(by_language, dict) or not by_language:
            continue
        for language in ("ENG", *sorted(by_language)):
            url = by_language.get(language)
            if isinstance(url, str) and url:
                return url

    return None


def _is_multi_lot(payload: dict[str, Any], lot_ids: Sequence[str]) -> bool:
    """True when the notice really has more than one lot.

    ``identifier-lot`` is TED's own list of lot identifiers and settles the
    question outright; it was present on 2,091 of 2,158 notices. Only when it is
    absent do the lot-level arrays stand in, counted before deduplication because
    six identical lot descriptions are still six lots (406326-2026).

    Arrays disagreeing in *length* is never consulted. That means we cannot
    associate their values, which is a different fact from how many lots exist.
    """
    if lot_ids:
        return len(lot_ids) > 1

    for field in LOT_ARRAY_FIELDS:
        value = payload.get(field)
        if isinstance(value, dict):
            if any(len(_as_list(entry)) > 1 for entry in value.values()):
                return True
        elif len(_as_list(value)) > 1:
            return True
    return False


# ------------------------------------------------------------- across the lots


def _map_durations(payload: dict[str, Any]) -> list[ContractDuration]:
    """Every distinct contract length the notice states, value and unit together.

    TED sends ``{"value": "10", "unit": "MONTH"}``, so the two halves arrive
    already joined and the separate ``duration-period-value-lot`` and
    ``duration-period-unit-lot`` fields are not requested. A bare 10 would be
    meaningless and a guessed unit would be worse.
    """
    durations: list[ContractDuration] = []
    for item in _as_list(payload.get("contract-duration-period-lot")):
        if not isinstance(item, dict):
            continue
        value = _text(item.get("value"))
        if value is None:
            continue
        duration = ContractDuration(value=value, unit=_text(item.get("unit")))
        if duration not in durations:
            durations.append(duration)
    return durations


def _map_start_dates(payload: dict[str, Any]) -> list[date]:
    """Every distinct contract start date, in the order given, earliest kept as given."""
    days: list[date] = []
    for parsed in _parse_dates(_as_list(payload.get("contract-duration-start-date-lot"))):
        day = parsed.date()
        if day not in days:
            days.append(day)
    return days


def _map_criteria(
    payload: dict[str, Any], source_id: str
) -> tuple[list[AwardCriterion], list[SelectionCriterion], bool]:
    """The award and selection criteria, paired by position only when it is safe.

    Each criterion's parts arrive as parallel arrays. They are read by position
    only when every array that is present has the same length - the same rule the
    deadline date and time arrays follow. Measured over 3,080 notices, the award
    arrays disagree on 2.5% of the notices carrying them and the selection arrays
    on none; 395741-2026 is the worst, 113 types against 157 numbers.

    Where they disagree nothing is paired, because a criterion carrying another
    criterion's weight looks entirely plausible and would never be caught. The
    arrays stay in ``raw`` and the caller is told.
    """
    award, award_ok = _pair(payload, AWARD_CRITERION_FIELDS, AwardCriterion)
    selection, selection_ok = _pair(payload, SELECTION_CRITERION_FIELDS, SelectionCriterion)

    unpaired = not (award_ok and selection_ok)
    if unpaired:
        log.warning("ted_criterion_arrays_disagree", source_id=source_id)

    return award, selection, unpaired


def _pair[ModelT](
    payload: dict[str, Any],
    fields: Sequence[tuple[str, str]],
    model: type[ModelT],
) -> tuple[list[ModelT], bool]:
    """Build one record per position, or nothing at all. False means nothing was built."""
    columns: dict[str, list[str | None]] = {}
    for attribute, field in fields:
        values = _entries(payload.get(field))
        if values is not None:
            columns[attribute] = values

    if not columns:
        return ([], True)

    lengths = {len(values) for values in columns.values()}
    if len(lengths) > 1:
        return ([], False)

    count = lengths.pop()
    return (
        [
            model(**{name: values[index] for name, values in columns.items()})
            for index in range(count)
        ],
        True,
    )


def _entries(value: Any) -> list[str | None] | None:
    """One field's values in order, or None when the field is absent.

    A multilingual field contributes one language only - the alphabetically first,
    so the choice is repeatable. Positions are preserved, including empty ones, so
    that a blank in the middle does not shift every criterion after it.
    """
    if value is None:
        return None

    if isinstance(value, dict):
        by_language = _language_map(value)
        language = _alphabetically_first(by_language)
        if language is None:
            return None
        return [_text(item) for item in by_language[language]]

    items = _as_list(value)
    return [_text(item) for item in items] if items else None


# --------------------------------------------------------------- shape helpers


def _language_map(value: Any) -> dict[str, list[str]]:
    """Both multilingual shapes as one: ``{lang: [text, ...]}``.

    ``{lang: "text"}`` and ``{lang: ["text", ...]}`` both occur, on different
    fields, in the same response.
    """
    if not isinstance(value, dict):
        return {}

    out: dict[str, list[str]] = {}
    for language, texts in value.items():
        key = str(language).strip().lower()
        items = [text for text in (_text(item) for item in _as_list(texts)) if text]
        if key and items:
            out[key] = items
    return out


def _preferred_language(
    by_language: dict[str, list[str]],
    official_languages: Sequence[str],
    *args: str | None,
) -> str | None:
    """Which language variant to show.

    English first, but only when the buyer actually published one - this reads
    ``title-proc``, never TED's translation of the composed title. Then the first
    official language of the notice that has text. Then the alphabetically first
    key, so that a notice with neither resolves the same way every time rather
    than by dictionary order.
    """
    if not by_language:
        return None

    if ENGLISH in by_language:
        return ENGLISH

    for preferred in args:
        if preferred and preferred in by_language:
            return preferred

    for code in official_languages:
        key = code.strip().lower()
        if key in by_language:
            return key

    return _alphabetically_first(by_language)


def _alphabetically_first(by_language: dict[str, list[str]]) -> str | None:
    return sorted(by_language)[0] if by_language else None


def _flatten(by_language: dict[str, list[str]]) -> list[str]:
    return [text for language in sorted(by_language) for text in by_language[language]]


def _as_list(value: Any) -> list[Any]:
    """TED sends a scalar on some fields and a one-element list on others."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _distinct_text(value: Any) -> list[str]:
    """Every distinct non-empty string in a field, in the order the source gave them.

    Lot-scoped fields repeat once per lot - six identical document links on
    598884-2026 - and the notice states one set of facts, not six.
    """
    seen: list[str] = []
    for item in _as_list(value):
        text = _text(item)
        if text is not None and text not in seen:
            seen.append(text)
    return seen


def _codes(value: Any, *, upper: bool = True) -> list[str]:
    """Distinct codes, upper-cased by default because TED is inconsistent about case."""
    codes: list[str] = []
    for item in _as_list(value):
        text = _text(item)
        if text is None:
            continue
        code = text.upper() if upper else text
        if code not in codes:
            codes.append(code)
    return codes


def _first(value: Any) -> Any:
    items = _as_list(value)
    return items[0] if items else None


def _text(value: Any) -> str | None:
    """A trimmed string, or None. An empty string is not a fact."""
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, int | float | Decimal):
        return str(value)
    return None


def _unique[T](values: Iterable[T]) -> list[T]:
    seen: list[T] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen
