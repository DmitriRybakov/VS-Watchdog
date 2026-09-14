"""Jinja2 environment, asset locations and the few filters templates may use.

Kept out of app.py so route modules can render without importing the app.

Autoescaping is on for every template, so all source and model text is escaped by
default. The filters here exist for the two things escaping does not cover:

- **``safe_url``** - a link whose scheme is not http or https is dropped. Source
  URLs come from an external system and a template must not be able to put
  ``javascript:`` into an href.
- **the small formatters** - a date, a days-left badge, a band name. They are
  here rather than in a template because a template that does arithmetic is a
  template with business logic in it, and because every one of them has a rule
  attached: an unknown value reads as unknown and never as zero.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.templating import Jinja2Templates

from watchdog.core import vocabulary
from watchdog.core.codelists import (
    award_criterion_type_label,
    criterion_number,
    dps_usage_label,
    framework_agreement_label,
    language_name,
    language_names,
    main_activity_label,
    number_kind_label,
    procedure_type_label,
)
from watchdog.core.countries import country_name, country_names
from watchdog.web.reviewer import get_reviewer

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

# The only schemes a rendered link may use.
SAFE_SCHEMES = frozenset({"http", "https"})

# Under this many days, the deadline badge changes state as well as colour.
URGENT_DAYS = 14

# How a deadline type reads to somebody who is not a procurement lawyer. A
# participation request is not a bid deadline and must never be shown as one.
DEADLINE_TYPES = {
    "tender_submission": "Bid due",
    "participation_request": "Request to participate",
    "expression_of_interest": "Expression of interest",
    "unknown": "Deadline, type not stated",
}

BAND_LABELS = {"shortlist": "Shortlist", "review": "Review", "archive": "Archive"}

VERDICT_LABELS = {
    "relevant": "Relevant",
    "not_relevant": "Not relevant",
    "unsure": "Unsure",
}

# How we would go after it, if at all. A separate question from whether it is
# relevant, and the words are the ones the team uses when it discusses one.
BID_ROUTE_LABELS = {
    "not_assessed": "Not assessed",
    "direct_bid": "Direct bid",
    "partner_route": "Partner route",
    "watch": "Watch",
    "no_bid": "No bid",
}

# The rules-only grade in words. The number on its own invites being read as a
# score, which is the confusion the separate field exists to prevent.
EVIDENCE_LABELS = {
    0: "No evidence",
    1: "Weak evidence",
    2: "Partial evidence",
    3: "Strong evidence",
}

# The same grade as one word, for a sentence that already says "evidence".
EVIDENCE_WORDS = {0: "none", 1: "weak", 2: "partial", 3: "strong"}

# Three different absences, and a colleague has to be able to tell them apart:
# the platform sent no value, we have not fetched or extracted it yet, and no
# judgement has been made. One phrase for all three hides which it is, and the
# thing to do about each of them is different.
#
# The first says "the retrieved data", not "the notice". TED returned no value in
# that field; the fact may still be written in the notice text or in a tender
# document nobody has read yet. "Not in the notice" claims we looked and it was
# not there, which is more than we know and stops somebody going to look.
NOT_IN_NOTICE = "Not provided in the retrieved data"
NOT_RETRIEVED = "Not retrieved yet"
NOT_ASSESSED = "AI assessment has not run"

ABSENT = {"source": NOT_IN_NOTICE, "retrieval": NOT_RETRIEVED, "assessment": NOT_ASSESSED}

# The fourth thing a screening chip can say: a model was asked and did not
# answer. It is neither a score nor a grade, and it must not read as either.
ASSESSMENT_FAILED_LABEL = "Assessment failed"
ASSESSMENT_FAILED_HINT = (
    "The AI assessment ran and did not come back, so nothing judged this notice."
)

# How much description shows before the expander takes over. Nothing is cut: the
# remainder goes behind the expander whole.
DESCRIPTION_LEAD_CHARS = 1200

STRENGTH_LABELS = {"high": "high", "medium": "medium", "supporting": "supporting only"}


def safe_url(value: str | None) -> str | None:
    """The URL if it is http or https, otherwise nothing at all."""
    if not value:
        return None
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in SAFE_SCHEMES or not parts.netloc:
        return None
    return value.strip()


def day(value: date | datetime | None) -> str:
    """A calendar date, or an en dash. Never "None", never today's date."""
    if value is None:
        return "\u2013"
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def moment(value: datetime | None) -> str:
    """A UTC timestamp to the minute, labelled, so nobody reads it as local time.

    Converted here, not assumed. Everything stored is already UTC, but appending
    the word "UTC" to a value that is not one would be a wrong time that looks
    checked - and a deadline of 00:30 in Warsaw is 22:30 the *previous day* in
    UTC, so the date moves too, not only the clock.
    """
    if value is None:
        return "\u2013"
    if value.tzinfo is None:
        raise ValueError("a naive timestamp cannot be labelled UTC; attach its offset first")
    return f"{value.astimezone(UTC).strftime('%Y-%m-%d %H:%M')} UTC"


def days_badge(days: int | None) -> dict[str, str]:
    """How many days are left, as words and a state name. Never colour alone."""
    if days is None:
        return {"text": "No deadline given", "state": "unknown"}
    if days < 0:
        return {"text": f"Closed {abs(days)}d ago", "state": "closed"}
    if days == 0:
        return {"text": "Closes today", "state": "urgent"}
    if days <= URGENT_DAYS:
        return {"text": f"{days}d left", "state": "urgent"}
    return {"text": f"{days}d left", "state": "open"}


def deadline_type(value: str | None) -> str:
    return DEADLINE_TYPES.get(value or "unknown", "Deadline, type not stated")


def evidence_label(grade: int | None) -> str:
    if grade is None:
        return "Assessed"
    return EVIDENCE_LABELS.get(grade, f"Grade {grade}")


def evidence_word(grade: int | None) -> str:
    return EVIDENCE_WORDS.get(grade if grade is not None else -1, "not graded")


def thousands(value: object) -> str:
    """A count with thousands separators. 3,080 is read at a glance; 3080 is not."""
    try:
        return f"{int(value):,}"  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return str(value)


def run_outcome(counts: object) -> str:
    """What an update run actually did, as a sentence a colleague can act on.

    The raw counts read as nonsense in front of somebody who did not write them:
    "3,078 updated, 2 unchanged" sounds as though the register had held two
    notices before.

    "updated" is the one to be careful with. It counts notices where the fields we
    compare differ from what came back, which is not the same claim as "the buyer
    changed something": the first run after we start asking TED for a new field
    updates every notice that has one. So the wording stays on our side of the
    comparison - we updated what we hold - and the belt carries the caveat.
    """
    if not isinstance(counts, dict):
        return ""

    def number(key: str) -> int:
        value = counts.get(key, 0)
        return value if isinstance(value, int) else 0

    new = number("new")
    changed = number("updated")
    same = number("unchanged")
    seen_before = changed + same

    parts: list[str] = []
    if new:
        parts.append(f"{new:,} new notice{'' if new == 1 else 's'}.")
    elif seen_before:
        parts.append("No new notices.")

    if seen_before:
        already = f"Rechecked {seen_before:,} existing notice{'' if seen_before == 1 else 's'}"
        if changed and same:
            already += (
                f": updated our stored information for {changed:,}; {same:,} needed no changes"
            )
        elif changed:
            already += (
                f": updated our stored information for {'it' if changed == 1 else 'every one'}"
            )
        else:
            already += ": it needed no changes" if same == 1 else ": none needed changes"
        parts.append(already + ".")

    quarantined = number("quarantined")
    if quarantined:
        parts.append(
            f"{quarantined:,} could not be read and {'is' if quarantined == 1 else 'are'} "
            "held aside rather than dropped."
        )

    # Anything a run reported that this wording does not name - a screening run's
    # own counts, for instance. Shown rather than swallowed: a number a colleague
    # cannot see is a number nobody can question.
    said = {"new", "updated", "unchanged", "quarantined"}
    if number("fetched") == new + seen_before + quarantined + number("failed"):
        # Every fetched notice is already named above, so repeating the total adds
        # nothing. A total that does not add up is another matter and stays.
        said.add("fetched")
    rest = [
        f"{value:,} {key.replace('_', ' ')}"
        for key, value in counts.items()
        if key not in said and isinstance(value, int) and value
    ]
    if rest:
        parts.append(", ".join(rest).capitalize() + ".")

    if not parts:
        return "Nothing was read."
    return " ".join(parts)


def given(value: object, kind: str = "source") -> str:
    """A value, or the kind of nothing it is. Never one word for three absences."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return ABSENT.get(kind, NOT_IN_NOTICE)
    return str(value)


def country(code: str | None) -> str:
    """A country by name, falling back to a code we cannot name, never to a guess."""
    return country_name(code) or (code or "")


def countries(codes: list[str] | None) -> str:
    """Several countries by name, in the order the source gave them."""
    return ", ".join(country_names(codes or []))


def paragraphs(value: str | None) -> list[str]:
    """Source text as paragraphs. Split on blank lines only; nothing is reflowed."""
    if not value:
        return []
    return [
        block.strip()
        for block in re.split(r"\n\s*\n", value.replace("\r\n", "\n"))
        if block.strip()
    ]


def clamped(value: str | None) -> dict[str, list[str]]:
    """Paragraphs split into what shows and what the expander holds.

    A description is never shortened, only folded: everything past the lead goes
    behind the expander in full, so nothing on this page is text a person cannot
    get back to.
    """
    blocks = paragraphs(value)
    lead: list[str] = []
    used = 0
    for index, block in enumerate(blocks):
        if lead and used + len(block) > DESCRIPTION_LEAD_CHARS:
            return {"lead": lead, "rest": blocks[index:]}
        lead.append(block)
        used += len(block)
    return {"lead": lead, "rest": []}


def band_label(value: str | None) -> str:
    return BAND_LABELS.get(value or "", "Not screened")


def verdict_label(value: str | None) -> str:
    return VERDICT_LABELS.get(value or "", "Undecided")


def bid_route_label(value: str | None) -> str:
    return BID_ROUTE_LABELS.get(value or "", "Not assessed")


def strength_label(value: str | None) -> str:
    return STRENGTH_LABELS.get(value or "", value or "")


def percent(value: float | None) -> str:
    return "\u2013" if value is None else f"{round(value * 100)}%"


def words(value: str | None) -> str:
    """A vocabulary member as a phrase: ``ccs_co2`` reads as ``Ccs co2``."""
    return (value or "").replace("_", " ").strip().capitalize()


def language(code: str | None) -> str:
    """A language by name, falling back to a code we cannot name, never to a guess."""
    return language_name(code) or (code or "")


def languages(codes: list[str] | None) -> str:
    """Several languages by name, in the order the source gave them."""
    return ", ".join(language_names(codes or []))


def duration(value: object) -> str:
    """A contract length with the unit the source gave. Never a bare number."""
    if value is None:
        return "\u2013"
    amount = getattr(value, "value", None)
    unit = getattr(value, "unit", None)
    if amount is None:
        return "\u2013"
    if not unit:
        return f"{amount} (unit not stated)"
    word = unit.strip().lower()
    return f"{amount} {word if amount in {'1', '1.0'} else word + 's'}"


def criterion_weight(criterion: object) -> str:
    """A criterion's number written with its verified meaning, or bare.

    A rank of 1 and a weight of 1% are different claims and TED writes both as
    "1". Where the meaning is not stated the number is shown exactly as it
    arrived; see core.codelists.
    """
    number = getattr(criterion, "number", None)
    kind = getattr(criterion, "number_kind", None)
    written = criterion_number(number, kind)
    if written is None:
        return ""
    if number_kind_label(kind) is None:
        return f"{written} (TED did not say what this number means)"
    return written


def recording_as(request: Request) -> str | None:
    """The name this browser is recording under, for any page that has to say so.

    The feedback box is included once in the base template and rendered by every
    route, so it cannot be handed the name by a route. It is the same cookie the
    review controls use, read the same way.
    """
    return get_reviewer(request)


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["safe_url"] = safe_url
templates.env.filters["day"] = day
templates.env.filters["moment"] = moment
templates.env.filters["days_badge"] = days_badge
templates.env.filters["deadline_type"] = deadline_type
templates.env.filters["evidence_label"] = evidence_label
templates.env.filters["evidence_word"] = evidence_word
templates.env.filters["band_label"] = band_label
templates.env.filters["verdict_label"] = verdict_label
templates.env.filters["bid_route_label"] = bid_route_label
templates.env.filters["strength_label"] = strength_label
templates.env.filters["percent"] = percent
templates.env.filters["words"] = words
templates.env.filters["given"] = given
templates.env.filters["country"] = country
templates.env.filters["countries"] = countries
templates.env.filters["language"] = language
templates.env.filters["languages"] = languages
templates.env.filters["duration"] = duration
templates.env.filters["criterion_weight"] = criterion_weight
templates.env.filters["procedure_type"] = procedure_type_label
templates.env.filters["main_activity"] = main_activity_label
templates.env.filters["framework_agreement"] = framework_agreement_label
templates.env.filters["dps_usage"] = dps_usage_label
templates.env.filters["criterion_type"] = award_criterion_type_label
templates.env.filters["clamped"] = clamped
templates.env.filters["thousands"] = thousands
templates.env.filters["run_outcome"] = run_outcome
templates.env.globals["recording_as"] = recording_as
templates.env.globals["NOT_IN_NOTICE"] = NOT_IN_NOTICE
templates.env.globals["NOT_RETRIEVED"] = NOT_RETRIEVED
templates.env.globals["NOT_ASSESSED"] = NOT_ASSESSED
# The three absences by name, so a field can say which kind of nothing it is
# without a template deciding on the wording.
templates.env.globals["ABSENT"] = ABSENT
templates.env.globals["ASSESSMENT_FAILED_LABEL"] = ASSESSMENT_FAILED_LABEL
templates.env.globals["ASSESSMENT_FAILED_HINT"] = ASSESSMENT_FAILED_HINT
# The team's word for every register field. Read by the table headers and the
# detail page, so TED's vocabulary never reaches a colleague.
templates.env.globals["LABEL"] = vocabulary.label
