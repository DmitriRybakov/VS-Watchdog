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

from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.templating import Jinja2Templates

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

# The rules-only grade in words. The number on its own invites being read as a
# score, which is the confusion the separate field exists to prevent.
EVIDENCE_LABELS = {
    0: "No evidence",
    1: "Weak evidence",
    2: "Partial evidence",
    3: "Strong evidence",
}

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
    """A UTC timestamp to the minute, labelled, so nobody reads it as local time."""
    if value is None:
        return "\u2013"
    return f"{value.strftime('%Y-%m-%d %H:%M')} UTC"


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


def band_label(value: str | None) -> str:
    return BAND_LABELS.get(value or "", "Not screened")


def verdict_label(value: str | None) -> str:
    return VERDICT_LABELS.get(value or "", "Undecided")


def strength_label(value: str | None) -> str:
    return STRENGTH_LABELS.get(value or "", value or "")


def percent(value: float | None) -> str:
    return "\u2013" if value is None else f"{round(value * 100)}%"


def words(value: str | None) -> str:
    """A vocabulary member as a phrase: ``ccs_co2`` reads as ``Ccs co2``."""
    return (value or "").replace("_", " ").strip().capitalize()


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["safe_url"] = safe_url
templates.env.filters["day"] = day
templates.env.filters["moment"] = moment
templates.env.filters["days_badge"] = days_badge
templates.env.filters["deadline_type"] = deadline_type
templates.env.filters["evidence_label"] = evidence_label
templates.env.filters["band_label"] = band_label
templates.env.filters["verdict_label"] = verdict_label
templates.env.filters["strength_label"] = strength_label
templates.env.filters["percent"] = percent
templates.env.filters["words"] = words
