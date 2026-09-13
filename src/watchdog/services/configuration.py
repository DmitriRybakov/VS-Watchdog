"""The active configuration: read from the database, edited through the browser.

Until now the active rule set, scoring policy and mandate were the YAML files in
git. That works on a laptop and fails on a host in two ways at once: a file
written by the settings page is erased by the next deploy, and a scheduled job
running in a different container never sees it at all. So the database holds the
active version, and the files become the seed and the export format.

Four things this module is careful about.

**Version numbers are imported, never reset.** The rules are at version 3 and the
stored screening results are stamped with it. Seeding them as version 1 would make
ten thousand results claim a rule set that never existed. So the first load takes
the number out of the file, and after that the number only ever goes up.

**Seeding never overwrites.** ``seed_config`` does nothing when a kind already has
a row. A deploy ships a YAML file from the image; if that replaced what a
colleague saved last week, it would do so silently and on every deploy.

**Saving writes a new row.** Nothing is edited and nothing is deleted, so a result
stamped ``rules 3`` can still be read against the rules that produced it.

**The rules are frozen.** Step 7 is tuning a scoring policy, and tuning against a
moving vocabulary means never knowing whether a change in the distribution came
from the policy or from the words. The freeze was a comment; here it is a refusal,
and it covers the import path as well as the save path, because an import is a
save with a different source of the payload.

A screening run takes a :class:`ConfigSnapshot` once and holds it to the end. Two
notices in one run are never judged against different configurations, however many
times somebody presses Save while it is going.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from watchdog.core.clock import utc_now
from watchdog.core.enums import ConfigKind
from watchdog.core.logging import get_logger
from watchdog.core.models import ConfigVersion, ScreeningVersions
from watchdog.screening.config import (
    DEFAULT_CONFIG_PATH as RULES_PATH,
)
from watchdog.screening.config import (
    RulesConfig,
    parse_rules_config,
    write_yaml_document,
)
from watchdog.screening.policy import DEFAULT_POLICY_PATH, PolicyConfig, parse_policy
from watchdog.screening.profile import DEFAULT_PROFILE_PATH, Profile, parse_profile
from watchdog.storage.db import get_session_factory
from watchdog.storage.repository import Repository

log = get_logger(__name__)

# Where each kind is seeded from, and where ``config export`` writes it back to.
SEED_PATHS: dict[ConfigKind, Path] = {
    ConfigKind.RULES: RULES_PATH,
    ConfigKind.POLICY: DEFAULT_POLICY_PATH,
    ConfigKind.PROFILE: DEFAULT_PROFILE_PATH,
}

# The version the vocabulary is frozen at, until the step 10 recall audit. A rules
# save is refused above this unless somebody sets the override explicitly and says
# why. See docs/decisions/README.md, "Outstanding".
FROZEN_RULES_VERSION = 3

FREEZE_REASON = (
    "The rule vocabulary is frozen at version 3 until the step 10 audit. Step 7 is tuning "
    "the scoring policy, and tuning against a moving vocabulary means never knowing whether "
    "a change in the distribution came from the policy or from the words. Save it with the "
    "override ticked, and say in the note why this one cannot wait - a rule that crashes, or "
    "a false positive at high strength, are the two cases that qualify."
)

# What the seeder records as the author. Not a person, and it must not read as one.
SEED_AUTHOR = "seeded from config/*.yaml"


class ConfigInvalid(ValueError):
    """A configuration Watchdog will not store. Shown as plain sentences."""

    def __init__(self, kind: ConfigKind, problems: list[str]) -> None:
        self.kind = kind
        self.problems = problems
        super().__init__("; ".join(problems))


class ConfigFrozen(PermissionError):
    """A rules change refused by the freeze. Says why, and how to proceed anyway."""


@dataclass(frozen=True)
class ConfigSnapshot:
    """The three configurations as one object, taken once and held.

    A screening run is given one of these at the start and uses it throughout, so
    a save landing mid-run cannot make two notices in the same run answer to
    different rules.
    """

    rules: RulesConfig
    policy: PolicyConfig
    profile: Profile

    def versions(self, *, prompt_version: str | None = None) -> ScreeningVersions:
        """The version stamps that go onto every result this snapshot produces."""
        return ScreeningVersions(
            rules_version=self.rules.rules_version,
            policy_version=self.policy.policy_version,
            profile_version=self.profile.profile_version,
            prompt_version=prompt_version,
        )


# --------------------------------------------------------------------- reading


def snapshot(*, repository: Repository | None = None) -> ConfigSnapshot:
    """The configuration in force right now, seeded from the files if it is absent."""
    store = repository or Repository(get_session_factory())
    ensure_seeded(repository=store)

    return ConfigSnapshot(
        rules=parse_rules_config(_payload(store, ConfigKind.RULES)),
        policy=parse_policy(_payload(store, ConfigKind.POLICY)),
        profile=parse_profile(_payload(store, ConfigKind.PROFILE)),
    )


def active(kind: ConfigKind, *, repository: Repository | None = None) -> ConfigVersion:
    """The active version of one configuration, seeding from the file if needed."""
    store = repository or Repository(get_session_factory())
    ensure_seeded(repository=store, kinds=(kind,))
    found = store.active_config(kind)
    if found is None:  # pragma: no cover - ensure_seeded has just written one
        raise ConfigInvalid(
            kind, [f"no active {kind.value} configuration, and none could be seeded"]
        )
    return found


def history(kind: ConfigKind, *, repository: Repository | None = None) -> list[ConfigVersion]:
    store = repository or Repository(get_session_factory())
    return store.config_history(kind)


def ensure_seeded(
    *,
    repository: Repository | None = None,
    kinds: tuple[ConfigKind, ...] = tuple(ConfigKind),
) -> list[ConfigVersion]:
    """Load each YAML file into the database, once, keeping its own version number.

    Does nothing for a kind that already has any row at all - not only an active
    one. A kind with history has been managed here since; the file in the image is
    a seed, never an authority.
    """
    store = repository or Repository(get_session_factory())
    seeded: list[ConfigVersion] = []

    for kind in kinds:
        if store.highest_config_version(kind) > 0:
            continue

        payload = _read_file(kind)
        _validated(kind, payload)

        written = store.seed_config(
            ConfigVersion(
                kind=kind,
                version=int(payload.get("version") or 1),
                payload=_json_safe(payload),
                saved_by=SEED_AUTHOR,
                saved_at=utc_now(),
                note=(
                    f"First load of {SEED_PATHS[kind].as_posix()}. The version number is the "
                    "file's own: stored screening results are stamped with it."
                ),
            )
        )
        if written is not None:
            seeded.append(written)
            log.info("config_seeded", kind=kind.value, version=written.version)

    return seeded


# --------------------------------------------------------------------- writing


def save(
    kind: ConfigKind,
    payload: dict[str, Any],
    *,
    saved_by: str,
    note: str | None = None,
    override_freeze: bool = False,
    repository: Repository | None = None,
) -> ConfigVersion:
    """Validate a configuration and store it as a new version.

    The version is set here, one above the highest ever saved for this kind, and
    written into the payload as well, so the parsed object and the row can never
    disagree about which version a result was screened under.
    """
    store = repository or Repository(get_session_factory())
    ensure_seeded(repository=store, kinds=(kind,))

    name = " ".join((saved_by or "").split())
    if not name:
        raise ConfigInvalid(
            kind,
            [
                "A configuration change has to be recorded in somebody's name. "
                "Put yours in the 'Recording as' box at the top of the register."
            ],
        )

    current = store.active_config(kind)
    next_version = store.highest_config_version(kind) + 1

    proposed = dict(payload)
    proposed["version"] = next_version
    _validated(kind, proposed)
    proposed = _json_safe(proposed)

    if current is not None and _without_version(current.payload) == _without_version(proposed):
        raise ConfigInvalid(kind, ["Nothing changed, so nothing was saved."])

    if kind is ConfigKind.RULES and next_version > FROZEN_RULES_VERSION and not override_freeze:
        raise ConfigFrozen(FREEZE_REASON)

    written = store.save_config(
        ConfigVersion(
            kind=kind,
            version=next_version,
            payload=proposed,
            saved_by=name,
            saved_at=utc_now(),
            note=note,
        )
    )
    log.info(
        "config_saved",
        kind=kind.value,
        version=written.version,
        saved_by=name,
        override_freeze=override_freeze,
    )
    return written


def diff(
    kind: ConfigKind,
    payload: dict[str, Any],
    *,
    repository: Repository | None = None,
) -> list[str]:
    """The change a save would make, as unified diff lines. Empty means no change.

    Both sides are dumped through the same YAML writer, so a difference on screen
    is a difference in the data and never in the formatting.
    """
    store = repository or Repository(get_session_factory())
    current = store.active_config(kind)
    before = _as_yaml(_without_version(current.payload)) if current is not None else ""
    after = _as_yaml(_without_version(payload))

    return list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"{kind.value} (active)",
            tofile=f"{kind.value} (proposed)",
            lineterm="",
            n=2,
        )
    )


# ---------------------------------------------------------------- files and git


def export_to_files(
    *,
    repository: Repository | None = None,
    kinds: tuple[ConfigKind, ...] = tuple(ConfigKind),
) -> list[tuple[ConfigKind, Path, int]]:
    """Write the active versions back to config/*.yaml, so a change can be committed.

    This is the whole reason the files stay: a change made in the browser has to be
    reviewable in git like any other change to how the register scores.
    """
    store = repository or Repository(get_session_factory())
    written: list[tuple[ConfigKind, Path, int]] = []

    for kind in kinds:
        version = store.active_config(kind)
        if version is None:
            continue
        path = SEED_PATHS[kind]
        write_yaml_document(path, version.payload, header=_export_header(kind, version))
        written.append((kind, path, version.version))
        log.info("config_exported", kind=kind.value, version=version.version, path=str(path))

    return written


def import_from_files(
    *,
    saved_by: str,
    note: str | None = None,
    override_freeze: bool = False,
    repository: Repository | None = None,
    kinds: tuple[ConfigKind, ...] = tuple(ConfigKind),
) -> list[ConfigVersion]:
    """Load config/*.yaml in as new versions. The freeze applies here exactly as it
    does to a save from the browser: an import is a save whose payload came from a
    file, and a route that skipped the check would be a way round it."""
    store = repository or Repository(get_session_factory())
    saved: list[ConfigVersion] = []

    for kind in kinds:
        payload = _read_file(kind)
        current = store.active_config(kind)
        if current is not None and _without_version(current.payload) == _without_version(payload):
            continue
        saved.append(
            save(
                kind,
                payload,
                saved_by=saved_by,
                note=note or f"Imported from {SEED_PATHS[kind].as_posix()}.",
                override_freeze=override_freeze,
                repository=store,
            )
        )

    return saved


# ------------------------------------------------------------ the edit surfaces
#
# The browser cannot be asked to edit YAML, so each configuration gets a form, and
# the shape of that form is decided here rather than in a template. A template
# that knew which key a box wrote to would be a second copy of the schema, and the
# two would drift.


@dataclass(frozen=True)
class NumberField:
    """One editable number, addressed by its dotted path into the payload."""

    path: str
    label: str
    note: str
    minimum: float
    maximum: float
    step: float

    def value(self, payload: dict[str, Any]) -> Any:
        return _at(payload, self.path)


# The scoring policy's numbers, in the order the document reads. Caps are handled
# separately below because each one carries a condition as well as a ceiling.
POLICY_NUMBERS: tuple[NumberField, ...] = (
    NumberField(
        "weights.domain",
        "Weight: is it our subject matter",
        "The three weights must add up to 1, or a weighted score is no longer on the 0-5 scale.",
        0.0,
        1.0,
        0.01,
    ),
    NumberField(
        "weights.service",
        "Weight: is it work we do",
        "Advisory and early-phase study, against design, construction and supply.",
        0.0,
        1.0,
        0.01,
    ),
    NumberField(
        "weights.stage",
        "Weight: how early the project is",
        "Before a major commitment is locked in, rather than after.",
        0.0,
        1.0,
        0.01,
    ),
    NumberField(
        "bands.shortlist_min",
        "Shortlist from",
        "A score at or above this goes to the shortlist.",
        1,
        5,
        1,
    ),
    NumberField(
        "bands.review_min",
        "Review from",
        "Below the shortlist and at or above this, a person decides. Below it, archive.",
        1,
        5,
        1,
    ),
    NumberField(
        "rules_only.max_score",
        "Keyword grade: highest a notice can reach with no model",
        "Keyword evidence is not a judgement, so it cannot reach the shortlist on its own.",
        1,
        5,
        1,
    ),
    NumberField(
        "rules_only.strong_domain_and_activity",
        "Keyword grade: strong subject matter and a service term",
        "The best a rules-only notice can show.",
        1,
        5,
        1,
    ),
    NumberField(
        "rules_only.partial_evidence",
        "Keyword grade: partial evidence",
        "A subject-matter rule, or a service term, but not both.",
        1,
        5,
        1,
    ),
    NumberField(
        "rules_only.weak_evidence",
        "Keyword grade: weak evidence",
        "Supporting terms only.",
        1,
        5,
        1,
    ),
    NumberField(
        "rules_only.no_evidence",
        "Keyword grade: nothing matched",
        "Our vocabulary is English and a notice may not be, so this is not an archive.",
        1,
        5,
        1,
    ),
    NumberField(
        "fallbacks.excluded_by_rule",
        "Fallback: an exclusion archived it",
        "Scored, never deleted.",
        1,
        5,
        1,
    ),
    NumberField(
        "fallbacks.assessment_failed",
        "Fallback: the model was asked and did not answer",
        "Kept visible so it is not silently lost, and screened again next run.",
        1,
        5,
        1,
    ),
    NumberField(
        "fallbacks.insufficient_information",
        "Fallback: the notice said too little to judge",
        "Thin text routes to a person, never to the archive.",
        1,
        5,
        1,
    ),
    NumberField(
        "confidence.review_below",
        "Send to review below this confidence",
        "Confidence is how well evidenced a judgement is, not a chance it is relevant.",
        0.0,
        1.0,
        0.05,
    ),
    NumberField(
        "confidence.description_chars_full",
        "Description length that counts as full",
        "Shorter than this and the notice is treated as thin, which lowers confidence.",
        1,
        10000,
        50,
    ),
)


@dataclass(frozen=True)
class CapField:
    """One scoring cap: what it applies to, and the ceiling it imposes."""

    name: str
    when: str
    max_score: int
    note: str | None


def policy_caps(payload: dict[str, Any]) -> list[CapField]:
    caps = payload.get("caps") or {}
    return [
        CapField(
            name=name,
            when=str((body or {}).get("when", "")),
            max_score=int((body or {}).get("max_score", 5)),
            note=(body or {}).get("note"),
        )
        for name, body in caps.items()
    ]


def apply_policy_form(
    payload: dict[str, Any],
    numbers: dict[str, str],
    caps: dict[str, str],
) -> dict[str, Any]:
    """The active policy with the submitted numbers written into it.

    Values that are blank or unreadable are left exactly as they were, rather than
    silently becoming zero: a weight of zero and a box somebody did not fill in are
    opposite intentions.
    """
    updated = _deep_copy(payload)

    for field in POLICY_NUMBERS:
        raw = (numbers.get(field.path) or "").strip()
        if not raw:
            continue
        parsed = _number(raw, whole=field.step == 1)
        if parsed is None:
            raise ConfigInvalid(ConfigKind.POLICY, [f"{field.label}: {raw!r} is not a number"])
        _put(updated, field.path, parsed)

    existing = updated.get("caps") or {}
    for name, raw in caps.items():
        if name not in existing:
            continue
        parsed = _number(raw.strip(), whole=True)
        if parsed is None:
            raise ConfigInvalid(ConfigKind.POLICY, [f"cap {name}: {raw!r} is not a whole number"])
        existing[name] = {**(existing[name] or {}), "max_score": parsed}
    updated["caps"] = existing

    return updated


def apply_profile_form(
    payload: dict[str, Any],
    *,
    principle: str,
    mandate_yaml: str,
) -> dict[str, Any]:
    """The active mandate with the submitted text written into it.

    The mandate is a nested document and is edited as YAML, because flattening it
    into boxes would fix its shape in code - and the shape belongs to Entr, not to
    this tool. It is parsed here, so an unreadable one is refused before it is
    stored rather than at the next screening run.
    """
    updated = _deep_copy(payload)

    text = principle.strip()
    if not text:
        raise ConfigInvalid(
            ConfigKind.PROFILE,
            [
                "The governing principle cannot be empty: it is what stops geography and "
                "contract value reaching the relevance score."
            ],
        )
    updated["principle"] = text

    try:
        mandate = yaml.safe_load(mandate_yaml) or {}
    except yaml.YAMLError as exc:
        raise ConfigInvalid(
            ConfigKind.PROFILE, [f"The mandate is not readable YAML: {exc}"]
        ) from exc
    if not isinstance(mandate, dict):
        raise ConfigInvalid(ConfigKind.PROFILE, ["The mandate has to be a set of named sections."])
    updated["screening_mandate"] = mandate

    return updated


def mandate_yaml(payload: dict[str, Any]) -> str:
    """The mandate section as editable YAML."""
    return _as_yaml(payload.get("screening_mandate") or {})


def apply_rules_form(
    payload: dict[str, Any],
    edits: dict[str, dict[str, Any]],
    *,
    removed: set[str],
    added: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The active rule set with the submitted edits applied.

    Duplicate ids are caught by ``RulesConfig`` itself when the result is
    validated, which is the only place that check should live - an id identifies a
    rule in every stored screening result.
    """
    updated = _deep_copy(payload)
    rules: list[dict[str, Any]] = list(updated.get("rules") or [])

    kept: list[dict[str, Any]] = []
    for rule in rules:
        identifier = str(rule.get("id", ""))
        if identifier in removed:
            continue
        edit = edits.get(identifier)
        if edit is not None:
            rule = {**rule, **edit}
            rule = {key: value for key, value in rule.items() if value not in (None, [])}
        kept.append(rule)

    if added:
        kept.append({key: value for key, value in added.items() if value not in (None, [], "")})

    updated["rules"] = kept
    return updated


def lines_to_terms(text: str) -> list[str]:
    """One term per line, blanks dropped, order kept. What every term box submits."""
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


# ------------------------------------------------------------------- internals


def _at(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _put(payload: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = payload
    for part in parts[:-1]:
        nested = current.get(part)
        if not isinstance(nested, dict):
            nested = {}
            current[part] = nested
        current = nested
    current[parts[-1]] = value


def _number(raw: str, *, whole: bool) -> int | float | None:
    try:
        return int(raw) if whole else float(raw)
    except ValueError:
        return None


def _deep_copy(payload: dict[str, Any]) -> dict[str, Any]:
    """A copy nothing else holds a reference into, so an edit cannot leak."""
    copied: dict[str, Any] = yaml.safe_load(_as_yaml(payload)) or {}
    return copied


def _json_safe(value: Any) -> Any:
    """The payload in types a JSON column can hold.

    YAML reads ``updated: 2026-09-12`` as a ``date`` object, and a JSON column
    cannot store one. Written back as an ISO string, which every one of the three
    schemas parses back into a date on the way out - so nothing is lost and the
    export still writes a readable file.
    """
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _payload(store: Repository, kind: ConfigKind) -> dict[str, Any]:
    found = store.active_config(kind)
    if found is None:  # pragma: no cover - ensure_seeded ran first
        return _read_file(kind)
    return found.payload


def _read_file(kind: ConfigKind) -> dict[str, Any]:
    path = SEED_PATHS[kind]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigInvalid(
            kind, [f"{path.as_posix()} could not be read: {exc.strerror or exc}"]
        ) from exc
    loaded = yaml.safe_load(text) or {}
    if not isinstance(loaded, dict):
        raise ConfigInvalid(kind, [f"{path.as_posix()} is not a mapping"])
    return loaded


def _validated(kind: ConfigKind, payload: dict[str, Any]) -> None:
    """Parse the payload with the schema that owns it, or say what is wrong with it."""
    parsers = {
        ConfigKind.RULES: parse_rules_config,
        ConfigKind.POLICY: parse_policy,
        ConfigKind.PROFILE: parse_profile,
    }
    try:
        parsers[kind](payload)
    except ValidationError as exc:
        raise ConfigInvalid(kind, _problems(exc)) from exc
    except ValueError as exc:
        raise ConfigInvalid(kind, [str(exc)]) from exc


def _problems(exc: ValidationError) -> list[str]:
    """Pydantic's complaints as sentences a colleague can act on."""
    lines: list[str] = []
    for error in exc.errors():
        where = " \u2192 ".join(str(part) for part in error["loc"]) or "the document"
        lines.append(f"{where}: {error['msg']}")
    return lines


def _without_version(payload: dict[str, Any]) -> dict[str, Any]:
    """The payload with its version and date removed, for comparing content only."""
    return {key: value for key, value in payload.items() if key not in {"version", "updated"}}


def _as_yaml(payload: dict[str, Any]) -> str:
    rendered: str = yaml.safe_dump(
        payload, allow_unicode=True, sort_keys=False, default_flow_style=False, width=100
    )
    return rendered


def _export_header(kind: ConfigKind, version: ConfigVersion) -> str:
    return (
        f"# {kind.value} configuration, version {version.version}.\n"
        "#\n"
        "# WRITTEN BY `watchdog config export` FROM THE DATABASE, which is where the active\n"
        "# configuration lives. Comments are not preserved - put a justification in a `note:`\n"
        "# field, which is data and survives.\n"
        f"# Saved by {version.saved_by} at {version.saved_at.isoformat()}.\n"
    )
