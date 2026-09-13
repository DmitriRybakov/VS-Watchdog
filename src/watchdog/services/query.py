"""The one query service. Every interface asks the register through this module.

Three things it exists to guarantee:

- **Filtering, sorting and paging happen in SQL.** Nothing here loads a set and
  narrows it in Python; every narrowing becomes a condition the repository turns
  into SQL, so the first page of a filtered view costs the same whether the
  register holds two thousand notices or two hundred thousand.
- **A number on screen and the view it links to are the same query.** Each header
  count is produced from exactly the filter object its link carries. They cannot
  drift, because there is only one of them.
- **The same question gives the same answer to a page, an export and a test.**
  ``parse`` turns a query string into a :class:`RegisterQuery`; everything else
  takes that object.

What the register shows by default follows the provider, and is not fixed. With
no model configured nothing can ever be shortlisted - ``config/policy.yaml`` caps
a rules-only score at 3 and the shortlist band starts at 4 - so a fixed
shortlist-first default would open on an empty page today. Once assessments
exist, the same fixed default would hide the highest-scoring notices in the tool.
So the default is asked of the provider state each time; see :func:`default_bands`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from urllib.parse import urlencode

from watchdog.core.clock import utc_now
from watchdog.core.enums import (
    Band,
    ContractNature,
    Domain,
    NoticeStage,
    RunKind,
    SourcePlatform,
    Verdict,
)
from watchdog.core.models import (
    JobState,
    RegisterFacets,
    RegisterPage,
    RegisterRow,
    Run,
    TenderFilters,
)
from watchdog.core.settings import Settings, get_settings
from watchdog.storage.db import get_session_factory
from watchdog.storage.repository import MAX_PAGE_SIZE, REGISTER_SORT, SORT_KEYS, Repository

# What a person can read on one screen without scrolling past the decision. The
# triage card shows one notice at a time, so the page is the queue, not the work.
DEFAULT_PAGE_SIZE = 25
MIN_PAGE_SIZE = 5

# "Closing soon" everywhere: the header count, the saved view and the row badge.
CLOSING_SOON_DAYS = 14

# "New this week" counts back from today inclusive, so it is seven dates.
WEEK_DAYS = 7

# The one job name. This is a triggered job, not a scheduler with a queue.
PIPELINE_JOB = "pipeline"

# Sort keys a URL may carry, mapped to what a column header says. Anything not
# here is refused by name and never reaches SQL.
SORTS: dict[str, str] = {
    REGISTER_SORT: "Evidence strength",
    "score": "Score",
    "rules_only_score": "Evidence",
    "band": "Band",
    "deadline_date": "Deadline",
    "published_date": "Published",
    "title": "Title",
    "buyer_name": "Buyer",
    "buyer_country": "Buyer country",
    "confidence": "Confidence",
    "first_seen_at": "First seen",
}

# Provider names that mean "no model". Read from settings rather than by building
# a provider, because rendering a page must never construct a model client - and
# an optional SDK that is not installed would turn a page view into an ImportError.
_NO_MODEL = frozenset({"", "disabled", "off", "none"})

MODES = frozenset({"triage", "table"})

# What a URL writes to mean "no band restriction". A value rather than an absent
# parameter, because absent already means "give me the working set".
ALL_BANDS = "all"


class FilterError(ValueError):
    """A query string Watchdog will not guess at. Rendered as a plain sentence.

    Refusing is deliberate. Silently dropping a filter it cannot read would show a
    colleague a different set of notices from the one they asked for, with nothing
    on screen to say so - and a bookmarked view is exactly where that would happen.
    """


def ai_configured(settings: Settings | None = None) -> bool:
    """Whether a model is configured at all. Never constructs one."""
    resolved = settings or get_settings()
    return resolved.llm_provider.strip().lower() not in _NO_MODEL


def default_bands(*, ai_enabled: bool) -> list[Band]:
    """The working set at rest. Follows the provider; see the module docstring."""
    return [Band.SHORTLIST, Band.REVIEW] if ai_enabled else [Band.REVIEW]


@dataclass(frozen=True)
class RegisterQuery:
    """One parsed request for register rows: what to show, how, and how much.

    Kept apart from :class:`TenderFilters` because half of it is presentation -
    which mode, which row is highlighted, which page - and the repository has no
    business knowing any of that.
    """

    filters: TenderFilters
    sort: str = REGISTER_SORT
    descending: bool = True
    limit: int = DEFAULT_PAGE_SIZE
    offset: int = 0
    mode: str = "triage"
    # Which row in this page the triage card is showing. An index, not an id: it
    # survives a row leaving the set, which is what happens when a verdict is
    # recorded while "undecided only" is on.
    cursor: int = 0
    # The saved view this came from, when it came from one. Display only.
    view: str | None = None
    ai_enabled: bool = True

    @property
    def page(self) -> int:
        return self.offset // self.limit + 1

    def pages(self, total: int) -> int:
        return max(1, -(-total // self.limit))

    def as_params(self) -> dict[str, object]:
        """The canonical query string, as a dict. Round-trips through :func:`parse`."""
        filters = self.filters
        params: dict[str, object] = {}

        if filters.bands:
            params["band"] = [band.value for band in filters.bands]
        else:
            params["band"] = ALL_BANDS
        if filters.min_score is not None:
            params["score_min"] = filters.min_score
        if filters.max_score is not None:
            params["score_max"] = filters.max_score
        if filters.min_rules_score is not None:
            params["evidence_min"] = filters.min_rules_score
        if filters.domain is not None:
            params["domain"] = filters.domain.value
        if filters.rule_id is not None:
            params["rule"] = filters.rule_id
        if filters.reason_tag is not None:
            params["tag"] = filters.reason_tag
        if filters.buyer_country is not None:
            params["buyer_country"] = filters.buyer_country
        if filters.place_of_performance_country is not None:
            params["performance_country"] = filters.place_of_performance_country
        if filters.source is not None:
            params["source"] = filters.source.value
        if filters.notice_stage is not None:
            params["stage"] = filters.notice_stage.value
        if filters.contract_nature is not None:
            params["nature"] = filters.contract_nature.value
        if filters.published_from is not None:
            params["published_from"] = filters.published_from.isoformat()
        if filters.published_to is not None:
            params["published_to"] = filters.published_to.isoformat()
        if filters.closing_within_days is not None:
            params["closing"] = filters.closing_within_days
        if filters.text:
            params["q"] = filters.text
        if filters.verdict is not None:
            params["verdict"] = filters.verdict.value
        if filters.undecided_only:
            params["undecided"] = "1"
        if filters.unscreened_only:
            params["unscreened"] = "1"
        if filters.first_seen_run_id is not None:
            params["run"] = filters.first_seen_run_id
        if filters.include_past_deadline:
            params["past"] = "1"

        if self.sort != REGISTER_SORT:
            params["sort"] = self.sort
        if not self.descending:
            params["dir"] = "asc"
        if self.limit != DEFAULT_PAGE_SIZE:
            params["limit"] = self.limit
        if self.offset:
            params["offset"] = self.offset
        if self.mode != "triage":
            params["mode"] = self.mode
        if self.cursor:
            params["cursor"] = self.cursor
        if self.view:
            params["view"] = self.view

        return params

    def query_string(self, **changes: object) -> str:
        """The URL for this view, optionally with some parts changed."""
        params = {**self.as_params(), **changes}
        pairs: list[tuple[str, str]] = []
        for key, value in params.items():
            if value is None or value == "":
                continue
            if isinstance(value, list):
                pairs.extend((key, str(item)) for item in value)
            else:
                pairs.append((key, str(value)))
        return urlencode(pairs)


@dataclass(frozen=True)
class SavedView:
    """A named starting point. A link, so it can be bookmarked and shared."""

    key: str
    label: str
    description: str
    params: dict[str, object]

    @property
    def query_string(self) -> str:
        return _encode(self.params)


@dataclass(frozen=True)
class HeaderCount:
    """One number in the header strip, and the view clicking it opens."""

    key: str
    label: str
    count: int
    query_string: str
    # Shown under the number when the count needs a sentence to be honest.
    note: str | None = None


@dataclass
class RegisterView:
    """Everything one render of the register needs, and nothing it has to compute."""

    query: RegisterQuery
    page: RegisterPage
    counts: list[HeaderCount] = field(default_factory=list)
    views: list[SavedView] = field(default_factory=list)
    facets: RegisterFacets = field(default_factory=RegisterFacets)
    last_ingest: Run | None = None
    last_screen: Run | None = None
    job: JobState | None = None
    ai_enabled: bool = True
    # True when the register holds nothing at all: a different sentence from
    # "these filters matched nothing", and a different thing to do about it.
    empty_database: bool = False
    unscreened: int = 0
    as_of: date = field(default_factory=lambda: utc_now().date())

    @property
    def rows(self) -> list[RegisterRow]:
        return self.page.items

    @property
    def cursor(self) -> int:
        """The highlighted row, clamped to what is actually on this page."""
        if not self.page.items:
            return 0
        return max(0, min(self.query.cursor, len(self.page.items) - 1))

    @property
    def current(self) -> RegisterRow | None:
        return self.page.items[self.cursor] if self.page.items else None

    @property
    def sort_label(self) -> str:
        return SORTS.get(self.query.sort, self.query.sort)


def parse(
    params: Mapping[str, object],
    *,
    ai_enabled: bool,
    as_of: date | None = None,
) -> RegisterQuery:
    """Turn a query string into a request, or say exactly what is wrong with it.

    An absent filter takes the default for the provider state; a filter that is
    present but unreadable is an error rather than a default, because the two mean
    opposite things to whoever wrote the URL.
    """
    today = as_of or utc_now().date()
    reader = _Params(params)

    filters = TenderFilters(
        bands=_bands(reader, ai_enabled=ai_enabled),
        min_score=reader.int_between("score_min", 1, 5),
        max_score=reader.int_between("score_max", 1, 5),
        min_rules_score=reader.int_between("evidence_min", 0, 5),
        domain=_optional("domain", reader.one("domain"), Domain),
        rule_id=reader.one("rule"),
        reason_tag=reader.one("tag"),
        buyer_country=reader.one("buyer_country"),
        place_of_performance_country=reader.one("performance_country"),
        source=_optional("source", reader.one("source"), SourcePlatform),
        notice_stage=_optional("stage", reader.one("stage"), NoticeStage),
        contract_nature=_optional("nature", reader.one("nature"), ContractNature),
        published_from=reader.date("published_from"),
        published_to=reader.date("published_to"),
        closing_within_days=reader.int_between("closing", 0, 365),
        text=reader.one("q"),
        verdict=_optional("verdict", reader.one("verdict"), Verdict),
        undecided_only=reader.flag("undecided"),
        unscreened_only=reader.flag("unscreened"),
        first_seen_run_id=reader.one("run"),
        # A passed deadline is a filter, never an archive. Off by default so the
        # first screen is work that can still be won; one click brings it back.
        include_past_deadline=reader.flag("past"),
        as_of=today,
    )

    sort = reader.one("sort") or REGISTER_SORT
    if sort not in SORTS or (sort != REGISTER_SORT and sort not in SORT_KEYS):
        raise FilterError(
            f"{sort!r} is not a column this register can sort by. "
            f"The ones it can are: {', '.join(sorted(SORTS))}."
        )

    direction = (reader.one("dir") or "desc").lower()
    if direction not in {"asc", "desc"}:
        raise FilterError(f"{direction!r} is not a sort direction; write asc or desc.")

    mode = (reader.one("mode") or "triage").lower()
    if mode not in MODES:
        raise FilterError(f"{mode!r} is not a view mode; write triage or table.")

    limit = reader.int_between("limit", MIN_PAGE_SIZE, MAX_PAGE_SIZE) or DEFAULT_PAGE_SIZE
    offset = max(0, reader.int_between("offset", 0, 1_000_000) or 0)

    return RegisterQuery(
        filters=filters,
        sort=sort,
        descending=direction == "desc",
        limit=limit,
        offset=offset,
        mode=mode,
        cursor=max(0, reader.int_between("cursor", 0, MAX_PAGE_SIZE) or 0),
        view=reader.one("view"),
        ai_enabled=ai_enabled,
    )


def register_view(
    params: Mapping[str, object] | None = None,
    *,
    repository: Repository | None = None,
    settings: Settings | None = None,
    as_of: date | None = None,
) -> RegisterView:
    """Everything the register page shows, from one parsed request."""
    resolved = settings or get_settings()
    store = repository or Repository(get_session_factory())
    enabled = ai_configured(resolved)
    today = as_of or utc_now().date()

    query = parse(params or {}, ai_enabled=enabled, as_of=today)
    return build(query, repository=store, ai_enabled=enabled, as_of=today)


def build(
    query: RegisterQuery,
    *,
    repository: Repository,
    ai_enabled: bool,
    as_of: date | None = None,
) -> RegisterView:
    """Run one parsed request and gather the surrounding state the header needs."""
    today = as_of or utc_now().date()

    page = repository.list_register(
        query.filters,
        sort_by=query.sort,
        descending=query.descending,
        limit=query.limit,
        offset=query.offset,
    )
    for row in page.items:
        row.days_left = _days_left(row, today)

    total_held = repository.count_register(TenderFilters(as_of=today))
    last_ingest = repository.latest_run(RunKind.INGEST)

    return RegisterView(
        query=query,
        page=page,
        counts=header_counts(
            repository,
            ai_enabled=ai_enabled,
            as_of=today,
            last_ingest_run_id=last_ingest.run_id if last_ingest is not None else None,
        ),
        views=saved_views(ai_enabled=ai_enabled),
        facets=repository.register_facets(),
        last_ingest=last_ingest,
        last_screen=repository.latest_run(RunKind.SCREEN),
        job=repository.get_job(PIPELINE_JOB),
        ai_enabled=ai_enabled,
        empty_database=total_held == 0,
        unscreened=repository.count_register(TenderFilters(unscreened_only=True, as_of=today)),
        as_of=today,
    )


def header_counts(
    repository: Repository,
    *,
    ai_enabled: bool,
    as_of: date,
    last_ingest_run_id: str | None = None,
) -> list[HeaderCount]:
    """The counts in the header strip. Each one is the filter its link carries.

    Every number comes from the register as it is, not from a subset chosen to
    look tidy: "new this week" is publication dates, not the notices that happen
    to carry evidence.
    """
    working = default_bands(ai_enabled=ai_enabled)
    week_from = as_of - timedelta(days=WEEK_DAYS - 1)

    wanted: list[tuple[str, str, str | None, dict[str, object]]] = []

    if last_ingest_run_id is not None:
        wanted.append(
            (
                "new_since_run",
                "New since last update",
                None,
                {"run": last_ingest_run_id, "past": "1"},
            )
        )

    wanted.append(
        (
            "new_this_week",
            "Published this week",
            "All bands, strongest evidence first",
            {"published_from": week_from.isoformat(), "band": ALL_BANDS},
        )
    )

    if ai_enabled:
        wanted.append(("shortlist", "Shortlist", None, {"band": [Band.SHORTLIST.value]}))
        wanted.append(("review", "Needs review", None, {"band": [Band.REVIEW.value]}))
    else:
        wanted.append(
            (
                "evidence",
                "Strongest evidence",
                "Keyword and CPV matches, no judgement",
                {"band": [Band.REVIEW.value]},
            )
        )

    wanted.append(
        (
            "closing_soon",
            f"Closing within {CLOSING_SOON_DAYS} days",
            _closing_note(ai_enabled),
            {"closing": CLOSING_SOON_DAYS, "band": [band.value for band in working]},
        )
    )
    wanted.append(
        (
            "undecided",
            "Undecided",
            None,
            {"undecided": "1", "band": [band.value for band in working]},
        )
    )
    wanted.append(("archive", "Archived", None, {"band": [Band.ARCHIVE.value], "past": "1"}))

    counts: list[HeaderCount] = []
    for key, label, note, params in wanted:
        query = parse(params, ai_enabled=ai_enabled, as_of=as_of)
        counts.append(
            HeaderCount(
                key=key,
                label=label,
                count=repository.count_register(query.filters),
                query_string=_encode(params),
                note=note,
            )
        )
    return counts


def saved_views(*, ai_enabled: bool) -> list[SavedView]:
    """Named starting points, adapted to what the tool can actually claim today.

    With no model configured, "Shortlist" and "Needs review" are not offered at
    all rather than offered empty: a band nothing can ever reach teaches a
    colleague that the tool found nothing, when in fact it was never asked.
    """
    working = [band.value for band in default_bands(ai_enabled=ai_enabled)]
    week_from = (utc_now().date() - timedelta(days=WEEK_DAYS - 1)).isoformat()

    views: list[SavedView] = []

    if ai_enabled:
        views.append(
            SavedView(
                key="shortlist",
                label="Shortlist",
                description="Scored 4 or 5. Judged worth a look.",
                params={"band": [Band.SHORTLIST.value, Band.REVIEW.value], "view": "shortlist"},
            )
        )
        views.append(
            SavedView(
                key="review",
                label="Needs review",
                description="Held for a person to decide.",
                params={"band": [Band.REVIEW.value], "view": "review"},
            )
        )
    else:
        views.append(
            SavedView(
                key="evidence",
                label="Strongest evidence",
                description="Keyword and CPV matches, strongest first. No judgement.",
                params={"band": [Band.REVIEW.value], "view": "evidence"},
            )
        )

    views.append(
        SavedView(
            key="new",
            label="New this week",
            description="Published in the last seven days, whatever the evidence.",
            params={"published_from": week_from, "band": ALL_BANDS, "view": "new"},
        )
    )
    views.append(
        SavedView(
            key="closing",
            label="Closing soon",
            description=f"Deadline within {CLOSING_SOON_DAYS} days.",
            params={"closing": CLOSING_SOON_DAYS, "band": working, "view": "closing"},
        )
    )
    views.append(
        SavedView(
            key="undecided",
            label="Undecided",
            description="Nobody has recorded a verdict yet.",
            params={"undecided": "1", "band": working, "view": "undecided"},
        )
    )

    if ai_enabled:
        views.append(
            SavedView(
                key="project_intelligence",
                label="Project intelligence",
                description="Our domain, not our service. A project exists and is moving.",
                params={
                    "tag": "project_intelligence",
                    "band": ALL_BANDS,
                    "view": "project_intelligence",
                },
            )
        )

    views.append(
        SavedView(
            key="archive",
            label="Archive",
            description="Low priority, still searchable. Nothing is ever deleted.",
            params={"band": [Band.ARCHIVE.value], "past": "1", "view": "archive"},
        )
    )
    views.append(
        SavedView(
            key="all",
            label="Everything",
            description="Every notice held, including past deadlines.",
            params={"band": ALL_BANDS, "past": "1", "view": "all"},
        )
    )
    return views


def rows_for_export(
    query: RegisterQuery,
    *,
    repository: Repository,
    as_of: date | None = None,
    cap: int = 10_000,
) -> list[RegisterRow]:
    """Every row the current filter matches, paged through in SQL.

    Capped rather than unbounded: an export is built in memory before it is
    streamed, and the register is meant to grow.
    """
    today = as_of or utc_now().date()
    collected: list[RegisterRow] = []

    while len(collected) < cap:
        page = repository.list_register(
            query.filters,
            sort_by=query.sort,
            descending=query.descending,
            limit=MAX_PAGE_SIZE,
            offset=len(collected),
        )
        if not page.items:
            break
        collected.extend(page.items)
        if len(collected) >= page.total:
            break

    for row in collected[:cap]:
        row.days_left = _days_left(row, today)
    return collected[:cap]


# ------------------------------------------------------------------- internals


def _closing_note(ai_enabled: bool) -> str:
    return (
        "Shortlist and review only"
        if ai_enabled
        else "Review band only - corpus-wide this number is mostly archive"
    )


def _days_left(row: RegisterRow, today: date) -> int | None:
    """Whole days to the deadline. None stays None: unknown is not zero."""
    deadline = row.tender.deadline_date
    return (deadline - today).days if deadline is not None else None


def _encode(params: Mapping[str, object]) -> str:
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None or value == "":
            continue
        if isinstance(value, list | tuple):
            pairs.extend((key, str(item)) for item in value)
        else:
            pairs.append((key, str(value)))
    return urlencode(pairs)


def _bands(reader: _Params, *, ai_enabled: bool) -> list[Band]:
    """Which bands the URL asks for, or the working set when it says nothing.

    ``band=all`` is how a URL asks for no band restriction at all. It has to be a
    value rather than an absent parameter, because absent already means "give me
    the default", and the archive and "published this week" views need to say the
    other thing.
    """
    asked = reader.all("band")
    if not asked:
        return default_bands(ai_enabled=ai_enabled)
    if ALL_BANDS in {value.lower() for value in asked}:
        return []
    return [_member("band", value, Band) for value in asked]


def _member[EnumT: StrEnum](name: str, value: str, vocabulary: type[EnumT]) -> EnumT:
    try:
        return vocabulary(value)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in vocabulary)
        raise FilterError(f"{value!r} is not a valid {name}; the choices are: {allowed}.") from exc


def _optional[EnumT: StrEnum](
    name: str, value: str | None, vocabulary: type[EnumT]
) -> EnumT | None:
    return _member(name, value, vocabulary) if value else None


class _Params:
    """Reads a query string strictly, naming whatever it cannot read."""

    def __init__(self, params: Mapping[str, object]) -> None:
        self._params = params

    def all(self, name: str) -> list[str]:
        value = self._params.get(name)
        if value is None:
            return []
        if isinstance(value, list | tuple):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        return [text] if text else []

    def one(self, name: str) -> str | None:
        found = self.all(name)
        return found[0] if found else None

    def flag(self, name: str) -> bool:
        value = self.one(name)
        return value is not None and value.lower() in {"1", "true", "yes", "on"}

    def int_between(self, name: str, low: int, high: int) -> int | None:
        value = self.one(name)
        if value is None:
            return None
        try:
            number = int(value)
        except ValueError as exc:
            raise FilterError(f"{name} must be a whole number, not {value!r}.") from exc
        if not low <= number <= high:
            raise FilterError(f"{name} must be between {low} and {high}; {number} is not.")
        return number

    def date(self, name: str) -> date | None:
        value = self.one(name)
        if value is None:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise FilterError(
                f"{name} must be a date written as YYYY-MM-DD, not {value!r}."
            ) from exc


def sort_options() -> Sequence[tuple[str, str]]:
    """The sort keys and their labels, for a select element."""
    return tuple(SORTS.items())
