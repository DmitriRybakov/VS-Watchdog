"""Country codes to readable names.

TED gives ISO 3166-1 alpha-3 codes: ``FRA``, not "France". The register shows
people a country, so the translation happens here and the stored value stays
exactly the code the source sent.

A code we do not know returns ``None`` rather than a guess or the code itself, so
the caller decides what to show. NUTS region codes such as ``FR101`` are *not*
resolved here - see docs/decisions/0003.
"""

from __future__ import annotations

from collections.abc import Iterable

# EU27, EEA, the UK and Switzerland cover every buyer we have seen. The rest are
# places of performance that turned up in sampling, plus the obvious neighbours.
_COUNTRY_NAMES: dict[str, str] = {
    "AUT": "Austria",
    "BEL": "Belgium",
    "BGR": "Bulgaria",
    "HRV": "Croatia",
    "CYP": "Cyprus",
    "CZE": "Czechia",
    "DNK": "Denmark",
    "EST": "Estonia",
    "FIN": "Finland",
    "FRA": "France",
    "DEU": "Germany",
    "GRC": "Greece",
    "HUN": "Hungary",
    "IRL": "Ireland",
    "ITA": "Italy",
    "LVA": "Latvia",
    "LTU": "Lithuania",
    "LUX": "Luxembourg",
    "MLT": "Malta",
    "NLD": "Netherlands",
    "POL": "Poland",
    "PRT": "Portugal",
    "ROU": "Romania",
    "SVK": "Slovakia",
    "SVN": "Slovenia",
    "ESP": "Spain",
    "SWE": "Sweden",
    "ISL": "Iceland",
    "LIE": "Liechtenstein",
    "NOR": "Norway",
    "GBR": "United Kingdom",
    "CHE": "Switzerland",
    "ALB": "Albania",
    "BIH": "Bosnia and Herzegovina",
    "MNE": "Montenegro",
    "MKD": "North Macedonia",
    "SRB": "Serbia",
    "TUR": "Türkiye",
    "UKR": "Ukraine",
    "MDA": "Moldova",
    "GEO": "Georgia",
    "USA": "United States",
    "CAN": "Canada",
    "AUS": "Australia",
    "JPN": "Japan",
    "MAR": "Morocco",
    "EGY": "Egypt",
    "ZAF": "South Africa",
    "IND": "India",
    "BRA": "Brazil",
    "CHL": "Chile",
    "CHN": "China",
    # Places of performance rather than buyers: EU institutions and member-state
    # agencies procure studies for projects well outside the EU, and the register
    # has to be able to say where. Curated, not exhaustive; see country_names.
    "AGO": "Angola",
    "DZA": "Algeria",
    "TUN": "Tunisia",
    "LBY": "Libya",
    "SEN": "Senegal",
    "GHA": "Ghana",
    "NGA": "Nigeria",
    "KEN": "Kenya",
    "ETH": "Ethiopia",
    "TZA": "Tanzania",
    "UGA": "Uganda",
    "MOZ": "Mozambique",
    "NAM": "Namibia",
    "ZMB": "Zambia",
    "BWA": "Botswana",
    "CMR": "Cameroon",
    "MLI": "Mali",
    "NER": "Niger",
    "BFA": "Burkina Faso",
    "JOR": "Jordan",
    "LBN": "Lebanon",
    "ISR": "Israel",
    "SAU": "Saudi Arabia",
    "ARE": "United Arab Emirates",
    "QAT": "Qatar",
    "OMN": "Oman",
    "IRQ": "Iraq",
    "KAZ": "Kazakhstan",
    "UZB": "Uzbekistan",
    "AZE": "Azerbaijan",
    "ARM": "Armenia",
    "PAK": "Pakistan",
    "BGD": "Bangladesh",
    "LKA": "Sri Lanka",
    "NPL": "Nepal",
    "IDN": "Indonesia",
    "VNM": "Viet Nam",
    "THA": "Thailand",
    "PHL": "Philippines",
    "MYS": "Malaysia",
    "SGP": "Singapore",
    "KOR": "South Korea",
    "MEX": "Mexico",
    "COL": "Colombia",
    "PER": "Peru",
    "ARG": "Argentina",
    "URY": "Uruguay",
    "NZL": "New Zealand",
}

# The buyer geographies the starting configuration ships with.
EU_EEA_UK: tuple[str, ...] = (
    "AUT",
    "BEL",
    "BGR",
    "HRV",
    "CYP",
    "CZE",
    "DNK",
    "EST",
    "FIN",
    "FRA",
    "DEU",
    "GRC",
    "HUN",
    "IRL",
    "ITA",
    "LVA",
    "LTU",
    "LUX",
    "MLT",
    "NLD",
    "POL",
    "PRT",
    "ROU",
    "SVK",
    "SVN",
    "ESP",
    "SWE",
    "ISL",
    "LIE",
    "NOR",
    "GBR",
)


def country_name(code: str | None) -> str | None:
    """The English name for an ISO 3166-1 alpha-3 code, or None if we do not know it."""
    if not code:
        return None
    return _COUNTRY_NAMES.get(code.strip().upper())


def country_names(codes: Iterable[str]) -> list[str]:
    """Readable names for display, falling back to the code where we cannot name one.

    The map is curated rather than the full ISO list, so a place of performance
    somewhere unexpected shows its code. That is visible and honest, and it is the
    moment at which to add the country here.
    """
    return [country_name(code) or code for code in codes]


def known_country_codes() -> frozenset[str]:
    """Every code this module can name. Used to validate a configured country list."""
    return frozenset(_COUNTRY_NAMES)
