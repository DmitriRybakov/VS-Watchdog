"""Codes the source sends, translated to readable names for display only.

TED answers in code: ``POL`` for Polish, ``neg-w-call`` for a negotiated
procedure with a prior call, ``per-exa`` for an exact percentage. The stored
value stays exactly the code TED sent; this module is the one place that turns a
code into words, so that "utility" never appears in the database and a code we do
not know is visible rather than guessed at.

An unknown code returns ``None``. The caller decides whether to show the raw code
or nothing at all - it never gets a plausible-looking invention.

**The award-criterion number codes were verified against real notices**, not read
off a specification, because getting one wrong turns a rank into a percentage:

- ``per-exa`` - a percentage. 596416-2026 states "waga 100%" beside number 100,
  and across 313 single-code notices the numbers total exactly 100.
- ``poi-exa`` - points, not percent. 541548-2026 names its criterion "Hinnan
  maksimipistemäärä", the maximum points for price; totals of 1000 and 0 also
  occur, which no percentage could.
- ``ord-imp`` - a rank, not a quantity at all. 534739-2026 scores Prijs 1 and
  Dienstverlening 2. Shown as a percentage this would read as 1%.
- ``dec-exa`` - a decimal fraction. 565418-2026 uses 0.8 and 0.2. Shown as a
  percentage this would be wrong by a factor of a hundred.
"""

from __future__ import annotations

from collections.abc import Iterable

# ISO 639-2/B, which is what TED sends in `official-language` and
# `submission-language`. Curated to the languages a European notice can be
# published or bid in, plus the regional languages that turn up in Spain.
_LANGUAGE_NAMES: dict[str, str] = {
    "BUL": "Bulgarian",
    "CES": "Czech",
    "DAN": "Danish",
    "DEU": "German",
    "ELL": "Greek",
    "ENG": "English",
    "EST": "Estonian",
    "FIN": "Finnish",
    "FRA": "French",
    "GLE": "Irish",
    "HRV": "Croatian",
    "HUN": "Hungarian",
    "ITA": "Italian",
    "LAV": "Latvian",
    "LIT": "Lithuanian",
    "MLT": "Maltese",
    "NLD": "Dutch",
    "POL": "Polish",
    "POR": "Portuguese",
    "RON": "Romanian",
    "SLK": "Slovak",
    "SLV": "Slovenian",
    "SPA": "Spanish",
    "SWE": "Swedish",
    "NOR": "Norwegian",
    "NOB": "Norwegian Bokmål",
    "NNO": "Norwegian Nynorsk",
    "ISL": "Icelandic",
    "CAT": "Catalan",
    "EUS": "Basque",
    "GLG": "Galician",
    "SQI": "Albanian",
    "SRP": "Serbian",
    "TUR": "Turkish",
    "UKR": "Ukrainian",
    "CYM": "Welsh",
    "MUL": "Several languages",
}

# TED's procedure-type codelist. What matters to us is whether a bidder can go
# straight to a tender or must qualify first; `has_qualification_stage` answers
# that rather than a template comparing strings.
_PROCEDURE_TYPES: dict[str, str] = {
    "open": "Open - anyone may tender",
    "restricted": "Restricted - qualify first, then tender",
    "neg-w-call": "Negotiated with a prior call for competition",
    "neg-wo-call": "Negotiated without a prior call for competition",
    "comp-dial": "Competitive dialogue",
    "innovation": "Innovation partnership",
    "oth-single": "Other, single stage",
    "oth-mult": "Other, multiple stages",
}

# Everything that is not a straight open procedure puts a qualification or
# negotiation stage in front of the bid. Measured over 2,158 notices: `open`
# 1,637, `neg-w-call` 321, `restricted` 68, `comp-dial` 9, other 16 - so reading
# this as "open versus restricted" would describe 3% of the population and
# mislead about the other 15%.
_SINGLE_STAGE = frozenset({"open", "oth-single"})

# TED's main-activity codelist: what the buying organisation does. The one
# distinction the mandate cares about is a utility against a general authority.
_MAIN_ACTIVITIES: dict[str, str] = {
    "gen-pub": "General public services",
    "defence": "Defence",
    "pub-os": "Public order and safety",
    "econ-aff": "Economic affairs",
    "env-pro": "Environmental protection",
    "hc-am": "Housing and community amenities",
    "health": "Health",
    "rcr": "Recreation, culture and religion",
    "education": "Education",
    "soc-pro": "Social protection",
    "electricity": "Electricity",
    "gas-heat": "Gas and heat production, transport and distribution",
    "gas-oil": "Extraction of gas or oil",
    "solid-fuel": "Exploration or extraction of coal or other solid fuels",
    "water": "Water",
    "post": "Postal services",
    "rail": "Railway services",
    "urttb": "Urban railway, tramway, trolleybus or bus services",
    "port": "Port-related activities",
    "airport": "Airport-related activities",
}

# The sectoral activities that make a buyer a utility rather than an authority.
_UTILITY_ACTIVITIES = frozenset(
    {
        "electricity",
        "gas-heat",
        "gas-oil",
        "solid-fuel",
        "water",
        "post",
        "rail",
        "urttb",
        "port",
        "airport",
    }
)

_FRAMEWORK_AGREEMENTS: dict[str, str] = {
    "none": "No framework agreement",
    "fa-w-rc": "Framework agreement, with reopening of competition",
    "fa-wo-rc": "Framework agreement, without reopening of competition",
    "fa-mix": "Framework agreement, partly with reopening of competition",
}

_DPS_USAGE: dict[str, str] = {
    "none": "No dynamic purchasing system",
    "dps-list": "Dynamic purchasing system, open to further buyers",
    "dps-nlist": "Dynamic purchasing system, closed to further buyers",
}

_AWARD_CRITERION_TYPES: dict[str, str] = {
    "price": "Price",
    "quality": "Quality",
    "cost": "Cost",
}

# The qualification bar, in words. TED sends "slc-stand-other" and
# "slc-abil-ref-services"; a colleague deciding whether we could bid needs
# "minimum turnover" and "reference projects". Only codes whose meaning is known
# are here - an unknown one returns None and the raw code is what gets shown.
_SELECTION_CRITERIA: dict[str, str] = {
    "slc-suit-reg-prof": "Enrolment in a professional register",
    "slc-suit-reg-trade": "Enrolment in a trade register",
    "slc-stand-ins": "Professional indemnity insurance",
    "slc-stand-to-gen": "Minimum yearly turnover",
    "slc-stand-to-spec": "Minimum turnover in this field of work",
    "slc-stand-to-avg": "Minimum average yearly turnover",
    "slc-stand-to-spec-avg": "Minimum average yearly turnover in this field of work",
    "slc-stand-rat": "Financial ratio",
    "slc-stand-acc": "How the bidder's accounts are set up",
    "slc-stand-other": "Other financial standing requirement",
    "slc-abil-ref-services": "Reference projects: services delivered",
    "slc-abil-ref-works": "Reference projects: works delivered",
    "slc-abil-ref-supp": "Reference projects: supplies delivered",
    "slc-abil-staff-yrly-avg-mp": "Average yearly staff numbers",
    "slc-abil-subc": "How much of the work may be subcontracted",
    "slc-abil-educ": "Educational and professional qualifications",
    "slc-abil-tech": "Technicians or technical bodies available",
    "slc-abil-equ": "Tools, plant and technical equipment",
    "slc-abil-qms": "Quality assurance scheme",
    "slc-abil-envm": "Environmental management measures",
    "slc-abil-samp": "Samples, descriptions or photographs",
    "slc-abil-cert": "Certificates from a quality-control institute",
    "slc-abil-study": "Study and research facilities",
    "slc-abil-meas": "Measures for ensuring quality",
    "slc-abil-syst": "Supply-chain management system",
}

# The three statutory categories the codes are grouped into, read off the code's
# own prefix rather than guessed. A code outside our list still lands in the
# right category, which is more use than the bare code and is not an invention.
_SELECTION_CRITERION_FAMILIES: tuple[tuple[str, str], ...] = (
    ("slc-suit", "Suitability to pursue the professional activity"),
    ("slc-stand", "Economic and financial standing"),
    ("slc-abil", "Technical and professional ability"),
)

# What the number beside an award criterion actually is. Verified; see the module
# docstring. An unknown code means the number is shown bare.
_NUMBER_KINDS: dict[str, str] = {
    "per-exa": "percentage",
    "per-mid": "percentage, middle of a range",
    "poi-exa": "points",
    "poi-mid": "points, middle of a range",
    "dec-exa": "decimal weighting",
    "dec-mid": "decimal weighting, middle of a range",
    "ord-imp": "rank by importance",
}

# How to write the number itself once its meaning is known. ``None`` means there
# is no safe suffix and the number is shown as it arrived.
_NUMBER_FORMATS: dict[str, str] = {
    "per-exa": "{value}%",
    "per-mid": "{value}%",
    "poi-exa": "{value} points",
    "poi-mid": "{value} points",
    "ord-imp": "ranked {value}",
}


def _lookup(table: dict[str, str], code: str | None, *, upper: bool = False) -> str | None:
    if not code:
        return None
    key = code.strip()
    return table.get(key.upper() if upper else key.lower())


def language_name(code: str | None) -> str | None:
    """The English name for an ISO 639-2 code, or None if we do not know it."""
    return _lookup(_LANGUAGE_NAMES, code, upper=True)


def language_names(codes: Iterable[str]) -> list[str]:
    """Readable names, falling back to the code where we cannot name one."""
    return [language_name(code) or code for code in codes]


def procedure_type_label(code: str | None) -> str | None:
    return _lookup(_PROCEDURE_TYPES, code)


def has_qualification_stage(code: str | None) -> bool | None:
    """True when bidders must get through a stage before tendering. None if unstated.

    Not "restricted": a negotiated procedure with a prior call has a selection
    stage too, and it is four times as common as a restricted one.
    """
    if not code:
        return None
    key = code.strip().lower()
    if key not in _PROCEDURE_TYPES:
        return None
    return key not in _SINGLE_STAGE


def main_activity_label(code: str | None) -> str | None:
    return _lookup(_MAIN_ACTIVITIES, code)


def is_utility_activity(code: str | None) -> bool | None:
    """True when the buyer is a utility rather than a general authority."""
    if not code:
        return None
    key = code.strip().lower()
    if key not in _MAIN_ACTIVITIES:
        return None
    return key in _UTILITY_ACTIVITIES


def framework_agreement_label(code: str | None) -> str | None:
    return _lookup(_FRAMEWORK_AGREEMENTS, code)


def dps_usage_label(code: str | None) -> str | None:
    return _lookup(_DPS_USAGE, code)


def award_criterion_type_label(code: str | None) -> str | None:
    return _lookup(_AWARD_CRITERION_TYPES, code)


def selection_criterion_label(code: str | None) -> str | None:
    """The qualification requirement in plain words, or None for a code we do not know."""
    return _lookup(_SELECTION_CRITERIA, code)


def selection_criterion_family(code: str | None) -> str | None:
    """Which of the three statutory categories a code belongs to, read off its prefix."""
    if not code:
        return None
    key = code.strip().lower()
    for prefix, name in _SELECTION_CRITERION_FAMILIES:
        if key == prefix or key.startswith(f"{prefix}-"):
            return name
    return None


def number_kind_label(code: str | None) -> str | None:
    """What the number beside a criterion means, or None when TED did not say."""
    return _lookup(_NUMBER_KINDS, code)


def criterion_number(value: str | None, kind: str | None) -> str | None:
    """The number written with its verified meaning, never as a bare percentage.

    A rank of 1 and a weight of 1% are different claims, and TED writes both as
    "1". Where the meaning is unknown the number is returned exactly as it
    arrived, so nothing on screen says more than the notice does.
    """
    if value is None or not value.strip():
        return None
    number = value.strip()
    template = _NUMBER_FORMATS.get((kind or "").strip().lower())
    return template.format(value=number) if template else number
