"""Exports of exactly what is on screen, produced by the same query service.

Three things this module is careful about.

**Formula injection.** A spreadsheet treats a cell beginning ``=``, ``+``, ``-``
or ``@`` as a formula, and a tender title is text a stranger wrote. Every text
cell is guarded on the way out, in both formats, so an export of the register
cannot be made to run something when a colleague opens it. The guard is an
apostrophe, which Excel and LibreOffice both read as "this is text": the value
stays legible and stays in the cell. It also stops openpyxl itself from deciding
that a string beginning ``=`` is a formula, which it would otherwise do.

**Types, where a type is the honest answer.** A date is written as a date and a
number as a number, so sorting or summing a column gives the right answer rather
than a lexicographic one. The exception is written down rather than assumed: a
CPV code is **text**, explicitly, because ``09310000`` is an identifier whose
leading zero is part of it, and a spreadsheet that reads it as nine million has
destroyed it. The same holds for country codes and rule set versions.

**Source text stays as it was written.** Nothing here normalises case, accents or
whitespace, and nothing translates. A title exported is the title stored, and an
unknown value is an empty cell - never ``None``, never ``0``.

The workbook is built in memory and streamed. Nothing is written to disk.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from openpyxl import Workbook
from openpyxl.cell.cell import Cell
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from watchdog.core import vocabulary
from watchdog.core.codelists import award_criterion_type_label, criterion_number
from watchdog.core.models import RegisterRow, Tender

# Characters that make a spreadsheet read a cell as a formula rather than text.
# The two whitespace ones matter because a leading tab or carriage return is
# stripped before the next character is considered.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

# What the guard puts in front. Read as "the rest of this cell is text".
GUARD = "'"


class Kind(StrEnum):
    """How one column should reach a spreadsheet.

    ``CODE`` is the one worth naming: it is text on purpose, for values that look
    numeric and are not. A CPV code, an ISO country code and a rule set version
    are identifiers; a spreadsheet that helpfully turns them into numbers has
    dropped a leading zero and left a workbook that no longer joins to anything.
    """

    TEXT = "text"
    CODE = "code"
    DATE = "date"
    TIMESTAMP = "timestamp"
    WHOLE = "whole"
    MONEY = "money"
    FRACTION = "fraction"


# The columns, in order, with what each one is. One definition: the CSV header,
# the workbook header and every cell type read it, so a column cannot be added to
# one and forgotten in the other.
COLUMN_KINDS: tuple[tuple[str, Kind], ...] = (
    ("id", Kind.CODE),
    ("source", Kind.TEXT),
    ("source_id", Kind.CODE),
    ("title", Kind.TEXT),
    ("title_native", Kind.TEXT),
    ("title_native_language", Kind.CODE),
    ("buyer_name", Kind.TEXT),
    ("buyer_country", Kind.CODE),
    ("country_of_performance", Kind.CODE),
    ("published_date", Kind.DATE),
    ("deadline_date", Kind.DATE),
    ("deadline_type", Kind.TEXT),
    ("days_left", Kind.WHOLE),
    ("notice_stage", Kind.TEXT),
    ("contract_nature", Kind.TEXT),
    ("cpv_main", Kind.CODE),
    ("estimated_value", Kind.MONEY),
    ("currency", Kind.CODE),
    # The "_across_lots" suffix is not decoration. TED states these per lot and
    # says nothing about which lot each belongs to, so a column headed
    # "submission_language" holding "CAT SPA" would read as both being accepted
    # for every lot. See docs/decisions/0008.
    ("lot_count", Kind.WHOLE),
    ("submission_languages_across_lots", Kind.CODE),
    ("procedure_type", Kind.TEXT),
    ("qualification_stage", Kind.TEXT),
    ("main_activity", Kind.TEXT),
    ("performance_city", Kind.TEXT),
    ("framework_agreement_across_lots", Kind.TEXT),
    ("contract_duration_across_lots", Kind.TEXT),
    # The criteria and their numbers are two columns, not one. TED sends the names
    # and the numbers as separate arrays and states no ordering between them, and
    # equal lengths do not prove equal order - on a single-lot notice either. A
    # wrong weight against the right criterion name is a mistake somebody acts on
    # and nobody catches. See docs/decisions/0008.
    ("award_criteria_across_lots", Kind.TEXT),
    ("award_criterion_numbers_unpaired", Kind.TEXT),
    ("submission_url_across_lots", Kind.TEXT),
    ("score", Kind.WHOLE),
    ("score_kind", Kind.TEXT),
    ("evidence_grade", Kind.WHOLE),
    ("band", Kind.TEXT),
    ("confidence", Kind.FRACTION),
    ("domains", Kind.TEXT),
    ("matched_rules", Kind.TEXT),
    ("reason_codes", Kind.TEXT),
    ("explanation", Kind.TEXT),
    ("review_verdict", Kind.TEXT),
    ("review_owner", Kind.TEXT),
    ("reviewed_by", Kind.TEXT),
    ("reviewed_at_utc", Kind.TIMESTAMP),
    ("screened_at_utc", Kind.TIMESTAMP),
    ("provider", Kind.TEXT),
    ("model", Kind.TEXT),
    ("rules_version", Kind.CODE),
    ("policy_version", Kind.CODE),
    ("source_url", Kind.TEXT),
)

COLUMNS: tuple[str, ...] = tuple(name for name, _ in COLUMN_KINDS)

# What a colleague reads at the top of the sheet. The column name is the stable
# key and never changes; the heading is the team's own word for the thing, from
# core.vocabulary, so a workbook exported here uses the same words as the register
# it came from. A column the vocabulary does not cover keeps its key.
COLUMN_LABELS: dict[str, str] = {
    "title": vocabulary.label("tender_name"),
    "buyer_name": vocabulary.label("client"),
    "buyer_country": vocabulary.label("client_country"),
    "country_of_performance": vocabulary.label("project_location"),
    "published_date": vocabulary.label("published_date"),
    "deadline_date": vocabulary.label("deadline_date"),
    "source": vocabulary.label("public_platform"),
    "notice_stage": vocabulary.label("phase_of_tender"),
    "submission_languages_across_lots": f"{vocabulary.label('submission_language')} (across lots)",
    "award_criteria_across_lots": f"{vocabulary.label('evaluation_and_award_criteria')} (names)",
    "award_criterion_numbers_unpaired": "Award criterion numbers (not matched to names)",
    "source_url": vocabulary.label("procurement_documents_link"),
}


def headings() -> list[str]:
    """The header row: the team's word for a column, or its key where there is none."""
    return [COLUMN_LABELS.get(name, name) for name in COLUMNS]


# How each kind is displayed. "@" is Excel's format code for "this cell is text",
# which is what keeps a leading zero on screen as well as in the file.
NUMBER_FORMATS: dict[Kind, str] = {
    Kind.TEXT: "General",
    Kind.CODE: "@",
    Kind.DATE: "yyyy-mm-dd",
    Kind.TIMESTAMP: "yyyy-mm-dd hh:mm",
    Kind.WHOLE: "0",
    Kind.MONEY: "#,##0.00",
    Kind.FRACTION: "0%",
}


def guard(value: str) -> str:
    """Prefix a cell that a spreadsheet would otherwise treat as a formula."""
    return GUARD + value if value.startswith(FORMULA_PREFIXES) else value


def _yes_no(value: bool | None) -> str | None:
    """A tri-state as a word. None stays empty rather than becoming "no"."""
    if value is None:
        return None
    return "yes" if value else "no"


def _criteria(tender: Tender) -> str | None:
    """The award criteria's names as one cell. Never with a number beside them.

    Empty when TED's arrays disagreed and nothing could be read as one list. The
    numbers go in their own column: TED sends the names and the numbers as two
    arrays with no stated ordering between them, so a name and a weight shown
    together in a spreadsheet would be an association nobody could check.
    """
    if tender.criteria_unpaired or not tender.award_criteria:
        return None

    parts: list[str] = []
    for item in tender.award_criteria:
        label = award_criterion_type_label(item.type) or item.type or "criterion"
        parts.append(item.name or item.description or label)
    return " | ".join(parts)


def _criterion_numbers(tender: Tender) -> str | None:
    """Every number the notice states beside its award criteria, in the order given."""
    if tender.criteria_unpaired or not tender.award_criteria:
        return None

    parts = [
        written
        for written in (
            criterion_number(item.number, item.number_kind) for item in tender.award_criteria
        )
        if written is not None
    ]
    return " | ".join(parts) or None


def values(row: RegisterRow) -> dict[str, Any]:
    """One register row as its real values, untyped and unformatted.

    Dates stay dates and numbers stay numbers here. Turning them into text is the
    writer's job, and only the CSV writer does it.
    """
    tender = row.tender
    screening = row.screening
    review = row.review

    found: dict[str, Any] = {
        "id": tender.id,
        "source": tender.source.value,
        "source_id": tender.source_id,
        "title": tender.title,
        "title_native": tender.title_native,
        "title_native_language": tender.title_native_language,
        "buyer_name": tender.buyer_name,
        "buyer_country": tender.buyer_country,
        "country_of_performance": " ".join(tender.place_of_performance_country),
        "published_date": tender.published_date,
        "deadline_date": tender.deadline_date,
        "deadline_type": tender.deadline_type.value,
        "days_left": row.days_left,
        "notice_stage": tender.notice_stage.value,
        "contract_nature": " ".join(nature.value for nature in tender.contract_natures),
        "cpv_main": tender.cpv_main,
        "estimated_value": tender.estimated_value,
        "currency": tender.currency,
        "lot_count": tender.lot_count,
        "submission_languages_across_lots": " ".join(tender.submission_languages),
        "procedure_type": tender.procedure_type,
        "qualification_stage": _yes_no(tender.has_qualification_stage),
        "main_activity": tender.main_activity,
        "performance_city": " ".join(tender.performance_cities),
        "framework_agreement_across_lots": " ".join(tender.framework_agreements),
        "contract_duration_across_lots": " ".join(
            f"{item.value}{'' if item.unit is None else ' ' + item.unit}"
            for item in tender.contract_durations
        ),
        "award_criteria_across_lots": _criteria(tender),
        "award_criterion_numbers_unpaired": _criterion_numbers(tender),
        "submission_url_across_lots": " ".join(tender.submission_urls),
        "source_url": tender.source_url,
    }

    if screening is not None:
        found |= {
            "score": screening.score,
            # Says which kind of claim the number beside it is, so a rules grade
            # read in a spreadsheet cannot be mistaken for a judged score.
            "score_kind": "assessed" if screening.assessment is not None else "rules_only",
            "evidence_grade": screening.rules_only_score,
            "band": screening.band.value,
            "confidence": screening.confidence,
            "domains": " ".join(domain.value for domain in screening.rules.domains_hit),
            "matched_rules": " ".join(
                dict.fromkeys(match.rule_id for match in screening.rules.matches)
            ),
            "reason_codes": " ".join(screening.reason_codes),
            "explanation": screening.explanation,
            "screened_at_utc": screening.created_at,
            "provider": screening.provider,
            "model": screening.model,
            "rules_version": screening.rules_version,
            "policy_version": screening.policy_version,
        }

    if review is not None:
        found |= {
            "review_verdict": review.verdict.value,
            "review_owner": review.owner,
            "reviewed_by": review.reviewed_by,
            "reviewed_at_utc": review.reviewed_at,
        }

    return found


def cells(row: RegisterRow) -> list[str]:
    """One register row as text, guarded. What the CSV writes."""
    found = values(row)
    return [guard(_text(found.get(name))) for name in COLUMNS]


def to_csv(rows: Sequence[RegisterRow]) -> Iterator[bytes]:
    """The export as CSV, a chunk at a time. UTF-8 with a BOM so Excel reads it.

    Everything is text here, because a CSV carries no types at all. The workbook
    is the export to open if you want to sort or sum a column.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")

    # Excel assumes the system code page for a plain UTF-8 CSV and mangles every
    # accented buyer name. The BOM is what tells it otherwise.
    yield "\ufeff".encode()

    writer.writerow(headings())
    yield _drain(buffer)

    for row in rows:
        writer.writerow(cells(row))
        yield _drain(buffer)


def to_xlsx(rows: Sequence[RegisterRow], *, sheet_name: str = "Register") -> bytes:
    """The export as a workbook, typed, built in memory and never written to disk."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = _sheet_name(sheet_name)

    sheet.append(headings())
    for heading in sheet[1]:
        heading.font = Font(bold=True)

    for row in rows:
        found = values(row)
        sheet.append([None] * len(COLUMN_KINDS))
        for cell, (name, kind) in zip(sheet[sheet.max_row], COLUMN_KINDS, strict=True):
            _write(cell, found.get(name), kind)

    # A thirty-seven column register is unreadable without these two.
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{sheet.max_row}"

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def filename(stem: str, extension: str, *, now: datetime | None = None) -> str:
    """A dated filename, so two exports taken a week apart do not collide."""
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%d")
    return f"{stem}-{stamp}.{extension}"


# ------------------------------------------------------------------- internals


def _write(cell: Cell, value: Any, kind: Kind) -> None:
    """Put one value in one cell, as the kind it is. Unknown stays empty."""
    cell.number_format = NUMBER_FORMATS[kind]

    if value is None or value == "":
        cell.value = None
        return

    if kind is Kind.TIMESTAMP and isinstance(value, datetime):
        # Excel has no concept of a timezone, so the moment is converted to UTC
        # and written naive. The column is named ..._utc for the same reason: the
        # file cannot carry the qualifier, so the header does.
        cell.value = value.astimezone(UTC).replace(tzinfo=None)
        return

    if kind is Kind.DATE and isinstance(value, date):
        cell.value = value
        return

    if kind is Kind.WHOLE and isinstance(value, int):
        cell.value = value
        return

    if kind is Kind.MONEY and isinstance(value, Decimal | int | float):
        cell.value = value
        return

    if kind is Kind.FRACTION and isinstance(value, int | float):
        cell.value = float(value)
        return

    # Everything else, and anything that was not the type its column expected, is
    # written as guarded text rather than guessed at. data_type is set explicitly
    # because openpyxl reads a leading "=" as a formula otherwise.
    cell.value = guard(_text(value))
    cell.data_type = "s"


def _text(value: Any) -> str:
    """A cell as text. None is empty - never "None", never 0."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="seconds")
    return str(value)


def _drain(buffer: io.StringIO) -> bytes:
    written = buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    return written.encode("utf-8")


def _sheet_name(name: str) -> str:
    """Excel refuses these characters in a sheet name, and caps it at 31."""
    safe = "".join(character for character in name if character not in "[]:*?/\\")[:31]
    return safe or "Register"
