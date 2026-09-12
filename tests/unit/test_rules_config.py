"""Loading, validating and saving config/rules.yaml.

The settings page will write this file, so the failure modes that matter here are
a rule set that does not validate and a save that is interrupted half way.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from watchdog.core.enums import RuleSignal, RuleStrength
from watchdog.screening import (
    RulesConfig,
    load_rules_config,
    next_version,
    parse_rules_config,
    save_rules_config,
)


def minimal(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "version": 1,
        "rules": [
            {
                "id": "domain_hydrogen",
                "signal": "domain",
                "label": "hydrogen",
                "strength": "high",
                "aliases": ["hydrogen", "H2"],
            }
        ],
    }
    data.update(overrides)
    return data


# ------------------------------------------------------------------- the seed


def test_the_shipped_rule_set_loads(rules_config: RulesConfig) -> None:
    assert rules_config.version >= 1
    assert rules_config.rules
    assert rules_config.cpv.archive_guard
    assert rules_config.rules_version == str(rules_config.version)


def test_every_signal_is_represented_in_the_seed(rules_config: RulesConfig) -> None:
    signals = {rule.signal for rule in rules_config.rules}

    assert signals == set(RuleSignal)


def test_supporting_domain_terms_exist_and_are_marked_as_such(
    rules_config: RulesConfig,
) -> None:
    supporting = [
        rule
        for rule in rules_config.rules
        if rule.signal is RuleSignal.DOMAIN and rule.strength is RuleStrength.SUPPORTING
    ]

    assert supporting, "the seed should carry supporting-only domain terms"
    assert all("energy" in rule.aliases for rule in supporting)


def test_the_archive_guard_is_not_the_collection_list(rules_config: RulesConfig) -> None:
    """Reusing config/sources/ted.yaml here would make cpv_match true for every
    notice we hold, and ARCHIVE_CANDIDATE unreachable."""
    from watchdog.sources.ted import load_ted_config

    collection = set(load_ted_config(Path("config/sources/ted.yaml")).cpv_prefixes)

    assert set(rules_config.cpv.archive_guard) != collection


# -------------------------------------------------------------------- validation


def test_a_rule_id_may_not_be_reused() -> None:
    data = minimal()
    data["rules"] = data["rules"] + [dict(data["rules"][0])]

    with pytest.raises(ValueError, match="more than once"):
        parse_rules_config(data)


def test_a_domain_rule_needs_a_label_from_the_vocabulary() -> None:
    data = minimal()
    data["rules"][0]["label"] = "hydrogene"

    with pytest.raises(ValueError, match="not a Domain"):
        parse_rules_config(data)


def test_a_domain_rule_without_a_label_is_rejected() -> None:
    data = minimal()
    del data["rules"][0]["label"]

    with pytest.raises(ValueError, match="needs a label"):
        parse_rules_config(data)


def test_an_exclusion_may_not_carry_a_label() -> None:
    data = minimal()
    data["rules"][0].update({"signal": "exclusion", "label": "out_of_scope"})

    with pytest.raises(ValueError, match="must not carry a label"):
        parse_rules_config(data)


def test_a_rule_needs_at_least_one_alias() -> None:
    data = minimal()
    data["rules"][0]["aliases"] = []

    with pytest.raises(ValueError):
        parse_rules_config(data)


def test_blank_and_duplicate_aliases_are_dropped() -> None:
    data = minimal()
    data["rules"][0]["aliases"] = ["hydrogen", "  ", "Hydrogen", " H2 "]

    config = parse_rules_config(data)

    assert config.rules[0].aliases == ["hydrogen", "H2"]


def test_an_unknown_field_selector_is_rejected() -> None:
    data = minimal()
    data["rules"][0]["fields"] = ["titel"]

    with pytest.raises(ValueError, match="unknown field"):
        parse_rules_config(data)


def test_a_field_selector_covers_the_blocks_beneath_it() -> None:
    rule = parse_rules_config(minimal()).rules[0]

    assert rule.reads("title-proc") is True
    assert rule.reads("description-proc") is True
    assert rule.reads("description-lot") is True
    assert rule.reads("buyer-name") is False


def test_a_cpv_code_that_is_not_a_code_is_rejected() -> None:
    with pytest.raises(ValueError, match="not a CPV code"):
        parse_rules_config(minimal(cpv={"archive_guard": ["energy"]}))


def test_a_cpv_code_keeps_its_leading_zero_when_yaml_makes_it_a_number() -> None:
    config = parse_rules_config(minimal(cpv={"archive_guard": [9300000]}))

    assert config.cpv.archive_guard == ["09300000"]


# ------------------------------------------------------------ prefix markers


def test_a_prefix_marker_is_accepted_on_a_domain_alias() -> None:
    data = minimal()
    data["rules"][0]["aliases"] = ["wasserstoff*"]

    assert parse_rules_config(data).rules[0].aliases == ["wasserstoff*"]


def test_a_prefix_marker_is_rejected_on_an_exclusion_alias() -> None:
    """A prefix widens recall on a domain term and widens archiving on an
    exclusion term. Those are opposite risks, so only one of them is allowed."""
    data = minimal()
    data["rules"][0] = {
        "id": "exclusion_building",
        "signal": "exclusion",
        "aliases": ["architekt*"],
    }

    with pytest.raises(ValueError, match="domain aliases only"):
        parse_rules_config(data)


def test_a_prefix_marker_is_rejected_on_a_context_or_companion_word() -> None:
    data = minimal()
    data["rules"][0]["requires_context"] = ["grid*"]

    with pytest.raises(ValueError, match="only allowed on an alias"):
        parse_rules_config(data)


def test_a_prefix_marker_belongs_at_the_end_of_an_alias() -> None:
    data = minimal()
    data["rules"][0]["aliases"] = ["wasser*stoff"]

    with pytest.raises(ValueError, match="at the end of an alias"):
        parse_rules_config(data)


def test_a_prefix_stem_must_be_long_enough_to_mean_something() -> None:
    data = minimal()
    data["rules"][0]["aliases"] = ["h2o*"]

    with pytest.raises(ValueError, match="at least"):
        parse_rules_config(data)


def test_the_seed_uses_prefixes_on_domain_rules_only(rules_config: RulesConfig) -> None:
    prefixed = [
        rule for rule in rules_config.rules if any(alias.endswith("*") for alias in rule.aliases)
    ]

    assert prefixed, "the seed should carry prefix aliases"
    assert all(rule.signal is RuleSignal.DOMAIN for rule in prefixed)


# ------------------------------------------------------ companion requirement


def test_the_seed_requires_a_companion_for_the_professional_service_terms(
    rules_config: RulesConfig,
) -> None:
    """HOAI, Leistungsphasen, the architect family and capacity building say how a
    service is priced or staffed, not what it is about. None may archive alone."""
    by_id = {rule.id: rule for rule in rules_config.rules}

    assert by_id["exclusion_building_profession"].requires_companion
    assert by_id["exclusion_capacity_building_generic"].requires_companion

    profession_aliases = {
        alias.casefold()
        for rule_id in ("exclusion_building_profession", "exclusion_capacity_building_generic")
        for alias in by_id[rule_id].aliases
    }
    assert {"hoai", "leistungsphasen", "architecte", "capacity building"} <= profession_aliases


def test_a_companion_requirement_survives_a_save(tmp_path: Path, rules_config: RulesConfig) -> None:
    target = tmp_path / "rules.yaml"

    save_rules_config(rules_config, target)
    reloaded = {rule.id: rule for rule in load_rules_config(target).rules}

    assert reloaded["exclusion_building_profession"].requires_companion == (
        {rule.id: rule for rule in rules_config.rules}[
            "exclusion_building_profession"
        ].requires_companion
    )


# ----------------------------------------------------------------- versioning


def test_next_version_increments_and_dates_the_rule_set(rules_config: RulesConfig) -> None:
    bumped = next_version(rules_config, today=date(2026, 10, 1))

    assert bumped.version == rules_config.version + 1
    assert bumped.updated == date(2026, 10, 1)
    assert bumped.rules == rules_config.rules


# -------------------------------------------------------------------- saving


def test_saving_round_trips_through_the_file(tmp_path: Path, rules_config: RulesConfig) -> None:
    target = tmp_path / "rules.yaml"

    save_rules_config(next_version(rules_config, today=date(2026, 10, 1)), target)
    reloaded = load_rules_config(target)

    assert reloaded.version == rules_config.version + 1
    assert [rule.id for rule in reloaded.rules] == [rule.id for rule in rules_config.rules]
    assert reloaded.cpv.archive_guard == rules_config.cpv.archive_guard
    assert reloaded.matching == rules_config.matching


def test_a_saved_file_keeps_the_notes_and_the_native_spellings(
    tmp_path: Path, rules_config: RulesConfig
) -> None:
    """Comments cannot survive PyYAML, which is why a justification is a note."""
    target = tmp_path / "rules.yaml"

    save_rules_config(rules_config, target)
    written = target.read_text(encoding="utf-8")
    reloaded = load_rules_config(target)

    assert written.startswith("# Deterministic screening rules")
    assert "wasserstoff" in written
    notes = {rule.id: rule.note for rule in reloaded.rules if rule.note}
    assert notes["activity_technical_assistance"]


def test_saving_validates_before_it_writes(tmp_path: Path, rules_config: RulesConfig) -> None:
    """``model_copy`` skips validation, so saving has to re-parse what it is given."""
    target = tmp_path / "rules.yaml"
    save_rules_config(rules_config, target)
    before = target.read_text(encoding="utf-8")

    broken = rules_config.model_copy(update={"rules": rules_config.rules + [rules_config.rules[0]]})

    with pytest.raises(ValueError, match="more than once"):
        save_rules_config(broken, target)

    assert target.read_text(encoding="utf-8") == before


def test_saving_leaves_no_temporary_file_behind(tmp_path: Path, rules_config: RulesConfig) -> None:
    target = tmp_path / "rules.yaml"

    save_rules_config(rules_config, target)

    assert [path.name for path in tmp_path.iterdir()] == ["rules.yaml"]


def test_saving_creates_the_directory_if_it_is_missing(
    tmp_path: Path, rules_config: RulesConfig
) -> None:
    target = tmp_path / "nested" / "rules.yaml"

    save_rules_config(rules_config, target)

    assert load_rules_config(target).rules


def test_the_written_file_is_plain_readable_yaml(tmp_path: Path, rules_config: RulesConfig) -> None:
    target = tmp_path / "rules.yaml"

    save_rules_config(rules_config, target)
    data = yaml.safe_load(target.read_text(encoding="utf-8"))

    assert list(data) == ["version", "updated", "owner", "matching", "cpv", "rules"]
    assert data["rules"][0]["signal"] == "domain"
