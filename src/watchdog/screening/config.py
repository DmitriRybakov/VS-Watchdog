"""The shape of config/rules.yaml, validated on the way in and on the way out.

Configuration is data. The vocabulary, the strengths, the required context words
and the CPV archive guard all live in the YAML file; nothing here repeats them.

Two things this file guarantees, because the settings page will write to it:

- A file that does not validate is never written. Saving re-parses what it is
  given, writes a temporary file beside the target and renames it into place, so
  an interrupted save cannot leave half a rule set behind.
- Every rule set carries a version. Each screening result records the version it
  was judged under, so a change marks earlier results stale rather than silently
  rewriting the past. ``next_version`` is how the settings page gets the next one.

Comments in the YAML file do not survive a machine rewrite - PyYAML cannot keep
them. That is why each rule carries a ``note`` field: a justification written
there is data and survives.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from watchdog.core.clock import utc_now
from watchdog.core.cpv import normalise_cpv
from watchdog.core.enums import (
    DecisionStage,
    Domain,
    RuleSignal,
    RuleStrength,
    ServiceType,
)

DEFAULT_CONFIG_PATH = Path("config/rules.yaml")

# Which blocks a rule reads. Source agnostic on purpose: a selector matches a
# block whose field is the selector itself or begins with it, so "description"
# covers both description-proc and description-lot on TED, and whatever a later
# source calls its description. A selector outside this set is a typo, and the
# settings page has to hear about it rather than save a rule that matches nothing.
FIELD_GROUPS = frozenset({"title", "description"})

# Trailing marker that makes an alias match a word it starts: "umspannwerk*" finds
# Umspannwerke and Bestandsumspannwerken. It is allowed on domain aliases only. A
# prefix on a domain term widens recall; a prefix on an exclusion term widens
# archiving, and those are opposite risks - see docs/decisions/0006.
PREFIX_MARKER = "*"

# Shortest stem a prefix may have. "h2*" would match half the periodic table.
MIN_PREFIX_STEM = 5

# A label is a member of the vocabulary its signal belongs to. Exclusions carry
# no label: "not our work" is not a domain, a service or a stage.
_LABEL_VOCABULARY: dict[RuleSignal, type[Domain] | type[ServiceType] | type[DecisionStage]] = {
    RuleSignal.DOMAIN: Domain,
    RuleSignal.ACTIVITY: ServiceType,
    RuleSignal.STAGE: DecisionStage,
}

# Written above every saved file, because the explanatory comments in the seed
# cannot survive PyYAML. Whoever opens the file after a save still needs to know
# what they are looking at.
_FILE_HEADER = """\
# Deterministic screening rules: vocabulary, aliases, required context words, CPV codes.
#
# WRITTEN BY WATCHDOG. Comments are not preserved when this file is saved from the settings
# page - put a justification in a rule's `note:` field, which is data and survives.
#
# A rule is one canonical concept with its aliases. Aliases are matched on the buyer's own
# words, on token boundaries, after normalising case, accents and hyphens for comparison
# only. requires_context words must appear within matching.context_window characters of the
# alias in the SAME block. A supporting rule is evidence but never establishes a domain.
#
# Nothing here rejects a tender: an exclusion routes to ARCHIVE_CANDIDATE only when no
# domain rule matched and no CPV code matched, and every tender is scored either way.
"""


class MatchingSettings(BaseModel):
    """How wide the matcher looks. Data, so it can be tuned without a release."""

    model_config = ConfigDict(frozen=True)

    # Characters either side of an alias, inside one block, in which a required
    # context word must appear.
    context_window: int = Field(default=300, ge=20, le=5000)
    # Roughly how much surrounding source text to keep as the evidence quote.
    evidence_chars: int = Field(default=120, ge=40, le=1000)


class CpvSettings(BaseModel):
    """CPV codes, used as an archive guard and never as evidence of relevance.

    See docs/decisions/0004. This is not the collection list in
    config/sources/ted.yaml: every notice we hold matched that one by
    construction, so reusing it would make ``cpv_match`` true for everything.
    """

    model_config = ConfigDict(frozen=True)

    archive_guard: list[str] = Field(default_factory=list)

    @field_validator("archive_guard", mode="before")
    @classmethod
    def _normalise(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value

        codes: list[str] = []
        for item in value:
            code = normalise_cpv(item)
            if code is None:
                raise ValueError(
                    f"{item!r} is not a CPV code; write it as eight digits, "
                    "quoted if it starts with a zero"
                )
            codes.append(code)
        return codes


class Rule(BaseModel):
    """One canonical concept, its aliases, and what it takes for them to count."""

    model_config = ConfigDict(frozen=True)

    id: str
    signal: RuleSignal
    # The vocabulary member this rule establishes: a Domain, a ServiceType or a
    # DecisionStage, depending on the signal. None for an exclusion.
    label: str | None = None
    strength: RuleStrength = RuleStrength.MEDIUM
    aliases: list[str] = Field(min_length=1)
    # Words that must appear near an alias for it to count. Applies to every alias
    # in the rule, which is why an ambiguous acronym gets a rule of its own.
    requires_context: list[str] = Field(default_factory=list)
    # Subject words that must appear somewhere in the notice - any block, no
    # proximity - before this rule counts at all. A different question from
    # requires_context: that one asks what an ambiguous word means here, this one
    # asks what the notice is about. Used to stop a term that names how a service
    # is priced or staffed from archiving a notice on its own.
    requires_companion: list[str] = Field(default_factory=list)
    # The mirror of requires_companion: words that stop this rule counting at all
    # when they appear anywhere in the notice. For the case a companion cannot
    # express - the word is genuinely there, and the notice is still about
    # something else. "Energieeffizienz" in a school's design brief is the case
    # this exists for; the measurement is in docs/eval/2026-09-12-domain-matches.md.
    blocked_by: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=lambda: ["title", "description"])
    active: bool = True
    # Why this rule exists, in plain language. Data, not a comment, so it survives
    # a save from the settings page.
    note: str | None = None

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        identifier = value.strip()
        if not identifier:
            raise ValueError("a rule needs an id")
        return identifier

    @field_validator(
        "aliases", "requires_context", "requires_companion", "blocked_by", mode="before"
    )
    @classmethod
    def _clean_terms(cls, value: Any) -> Any:
        """Drop blanks and duplicates, keeping the order they were written in."""
        if not isinstance(value, list):
            return value

        seen: set[str] = set()
        terms: list[str] = []
        for item in value:
            term = str(item).strip()
            if not term or term.casefold() in seen:
                continue
            seen.add(term.casefold())
            terms.append(term)
        return terms

    @field_validator("fields")
    @classmethod
    def _check_fields(cls, value: list[str]) -> list[str]:
        selectors = [item.strip().lower() for item in value]
        unknown = [item for item in selectors if item not in FIELD_GROUPS]
        if unknown:
            known = ", ".join(sorted(FIELD_GROUPS))
            raise ValueError(f"unknown field(s) {unknown}; the fields a rule can read are: {known}")
        return selectors or sorted(FIELD_GROUPS)

    @model_validator(mode="after")
    def _check_label(self) -> Rule:
        vocabulary = _LABEL_VOCABULARY.get(self.signal)

        if vocabulary is None:
            if self.label is not None:
                raise ValueError(
                    f"rule {self.id!r} is an exclusion and must not carry a label; "
                    "an exclusion says what the work is not, not what it is"
                )
            return self

        if self.label is None:
            raise ValueError(f"rule {self.id!r} needs a label from {vocabulary.__name__}")

        allowed = {member.value for member in vocabulary}
        if self.label not in allowed:
            raise ValueError(
                f"rule {self.id!r} has label {self.label!r}, which is not a "
                f"{vocabulary.__name__}; allowed: {', '.join(sorted(allowed))}"
            )
        return self

    @model_validator(mode="after")
    def _check_prefixes(self) -> Rule:
        for term in self.requires_context + self.requires_companion + self.blocked_by:
            if PREFIX_MARKER in term:
                raise ValueError(
                    f"rule {self.id!r}: {term!r} - a prefix marker is only allowed on an alias, "
                    "not on a context, companion or blocking word"
                )

        for alias in self.aliases:
            if PREFIX_MARKER not in alias:
                continue
            if self.signal is not RuleSignal.DOMAIN:
                raise ValueError(
                    f"rule {self.id!r}: {alias!r} - a prefix marker is allowed on domain aliases "
                    "only. On a domain term it widens recall; on an exclusion term it widens "
                    "archiving, which is the opposite risk"
                )
            if not alias.endswith(PREFIX_MARKER) or alias.count(PREFIX_MARKER) > 1:
                raise ValueError(
                    f"rule {self.id!r}: {alias!r} - a prefix marker goes at the end of an alias "
                    "and nowhere else; there is no wildcard in the middle of a term"
                )
            if len(alias) - 1 < MIN_PREFIX_STEM:
                raise ValueError(
                    f"rule {self.id!r}: {alias!r} - a prefix needs at least {MIN_PREFIX_STEM} "
                    "characters before the marker, or it matches half the dictionary"
                )
        return self

    def reads(self, field: str) -> bool:
        """True when a block from ``field`` is one this rule looks at.

        ``title`` matches ``title-proc``; ``description`` matches both
        ``description-proc`` and ``description-lot``.
        """
        name = field.strip().lower()
        return any(name == selector or name.startswith(f"{selector}-") for selector in self.fields)


class RulesConfig(BaseModel):
    """Everything the deterministic stage is driven by, in one validated object."""

    model_config = ConfigDict(frozen=True)

    version: int = Field(default=1, ge=1)
    updated: date | None = None
    owner: str | None = None
    matching: MatchingSettings = Field(default_factory=MatchingSettings)
    cpv: CpvSettings = Field(default_factory=CpvSettings)
    rules: list[Rule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique_ids(self) -> RulesConfig:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for rule in self.rules:
            if rule.id in seen:
                duplicates.add(rule.id)
            seen.add(rule.id)

        if duplicates:
            raise ValueError(
                f"rule id(s) used more than once: {', '.join(sorted(duplicates))}; "
                "an id identifies a rule in every stored screening result"
            )
        return self

    @property
    def rules_version(self) -> str:
        """The version as it is recorded on a screening result."""
        return str(self.version)

    def active_rules(self) -> list[Rule]:
        return [rule for rule in self.rules if rule.active]

    def to_yaml_dict(self) -> dict[str, Any]:
        """The mapping that gets written back, in the order a person reads it."""
        data: dict[str, Any] = {
            "version": self.version,
            "updated": self.updated,
            "owner": self.owner,
            "matching": {
                "context_window": self.matching.context_window,
                "evidence_chars": self.matching.evidence_chars,
            },
            "cpv": {"archive_guard": list(self.cpv.archive_guard)},
            "rules": [_rule_to_dict(rule) for rule in self.rules],
        }
        return {key: value for key, value in data.items() if value is not None}


def _rule_to_dict(rule: Rule) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": rule.id,
        "signal": rule.signal.value,
        "label": rule.label,
        "strength": rule.strength.value,
        "aliases": list(rule.aliases),
        "requires_context": list(rule.requires_context),
        "requires_companion": list(rule.requires_companion),
        "blocked_by": list(rule.blocked_by),
        "fields": list(rule.fields),
        "active": rule.active,
        "note": rule.note,
    }
    return {key: value for key, value in data.items() if value is not None}


def parse_rules_config(data: dict[str, Any]) -> RulesConfig:
    """Validate an already-loaded mapping. Pure, so it is easy to test."""
    return RulesConfig.model_validate(data or {})


def load_rules_config(path: Path | str = DEFAULT_CONFIG_PATH) -> RulesConfig:
    """Read the seed file.

    Callers are the service layer only. When the config_version table becomes the
    active source of configuration, this stays as the seed loader and the service
    reads the active version instead.
    """
    text = Path(path).read_text(encoding="utf-8")
    return parse_rules_config(yaml.safe_load(text) or {})


def next_version(config: RulesConfig, *, today: date | None = None) -> RulesConfig:
    """The same rules, one version on, dated today. What an edit is saved as."""
    return config.model_copy(
        update={"version": config.version + 1, "updated": today or utc_now().date()}
    )


def save_rules_config(config: RulesConfig, path: Path | str = DEFAULT_CONFIG_PATH) -> None:
    """Validate, then replace the file atomically. Never writes a broken rule set.

    Re-parsing what is about to be written is not belt and braces: a caller can
    build a ``RulesConfig`` by copying and updating one, which skips validation.
    """
    validated = parse_rules_config(config.to_yaml_dict())
    write_yaml_document(path, validated.to_yaml_dict(), header=_FILE_HEADER)


def write_yaml_document(
    path: Path | str, data: dict[str, Any], *, header: str | None = None
) -> None:
    """Write one YAML document, atomically, with an optional explanatory header.

    Written beside the target and renamed into place, so an interrupted save
    cannot leave half a configuration file behind. Shared with the policy and the
    profile, which are exported from the database the same way.
    """
    body = yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
    )

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Same directory, so the rename is on one filesystem and therefore atomic.
    handle, temporary = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            if header:
                stream.write(header)
                stream.write("\n")
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
