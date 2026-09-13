"""Configuration in the database: versions, the freeze, and what a change made stale.

The four things that would be expensive to get wrong, each with a test:

- **version numbers are imported, not reset.** The rules are at version 3 and ten
  thousand stored results are stamped with it;
- **seeding never overwrites**, or a deploy would silently replace what a
  colleague saved;
- **a save appends**, so a superseded configuration stays readable;
- **the freeze refuses**, on the import path as well as on the settings page.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from watchdog.core.enums import Band, ConfigKind, RulesRoute
from watchdog.core.models import (
    RulesResult,
    ScreeningResult,
    ScreeningVersions,
    Tender,
)
from watchdog.services import configuration as config_service
from watchdog.services.configuration import ConfigFrozen, ConfigInvalid
from watchdog.storage.repository import Repository

RUN = "run-settings-0001"


def _seeded(repository: Repository) -> None:
    config_service.ensure_seeded(repository=repository)


def _screened(
    repository: Repository,
    tender: Tender,
    *,
    rules_version: str,
    created_at: datetime,
) -> ScreeningResult:
    return repository.save_screening_result(
        ScreeningResult(
            tender_id=tender.id,
            score=3,
            band=Band.REVIEW,
            rules_only_score=2,
            confidence=0.5,
            rules=RulesResult(
                tender_id=tender.id,
                matches=[],
                domains_hit=[],
                route=RulesRoute.ASSESS,
                rules_version=rules_version,
            ),
            explanation="Keyword evidence only.",
            screened_content_hash=tender.content_hash,
            provider="disabled",
            rules_version=rules_version,
            policy_version="1",
            profile_version="1",
            created_at=created_at,
        )
    )


# --------------------------------------------------------------------- seeding


def test_seeding_keeps_the_files_own_version_number(repository: Repository) -> None:
    """Not version 1. Stored screening results are stamped with the file's number.

    Renumbering would make every one of those results claim a rule set that never
    existed.
    """
    _seeded(repository)

    on_file = yaml.safe_load(Path("config/rules.yaml").read_text(encoding="utf-8"))
    active = repository.active_config(ConfigKind.RULES)

    assert active is not None
    assert active.version == int(on_file["version"])
    assert active.version > 1, "the shipped rule set is not at version 1"


def test_seeding_does_nothing_where_a_version_already_exists(repository: Repository) -> None:
    """A deploy ships a file. It must not replace what somebody saved last week."""
    _seeded(repository)
    before = config_service.active(ConfigKind.POLICY, repository=repository)

    saved = config_service.save(
        ConfigKind.POLICY,
        config_service.apply_policy_form(before.payload, {"bands.review_min": "2"}, {}),
        saved_by="Ada Lovelace",
        note="review from 2",
        repository=repository,
    )

    seeded_again = config_service.ensure_seeded(repository=repository)

    assert seeded_again == []
    after = repository.active_config(ConfigKind.POLICY)
    assert after is not None
    assert after.version == saved.version
    assert after.saved_by == "Ada Lovelace"


def test_every_kind_is_seeded_and_validates(repository: Repository) -> None:
    _seeded(repository)
    snapshot = config_service.snapshot(repository=repository)

    assert snapshot.rules.rules_version
    assert snapshot.policy.policy_version
    assert snapshot.profile.principle


# ---------------------------------------------------------------------- saving


def test_a_save_writes_a_new_version_and_keeps_the_old_one(repository: Repository) -> None:
    _seeded(repository)
    before = config_service.active(ConfigKind.POLICY, repository=repository)

    proposed = config_service.apply_policy_form(before.payload, {"bands.review_min": "2"}, {})
    saved = config_service.save(
        ConfigKind.POLICY,
        proposed,
        saved_by="Ada Lovelace",
        note="review from 2",
        repository=repository,
    )

    assert saved.version == before.version + 1
    history = repository.config_history(ConfigKind.POLICY)
    assert [item.version for item in history] == [saved.version, before.version]
    assert history[1].active is False
    assert history[1].payload == before.payload, "nothing is edited, ever"


def test_a_save_needs_somebodys_name_on_it(repository: Repository) -> None:
    _seeded(repository)
    active = config_service.active(ConfigKind.POLICY, repository=repository)

    with pytest.raises(ConfigInvalid, match="somebody's name"):
        config_service.save(
            ConfigKind.POLICY,
            config_service.apply_policy_form(active.payload, {"bands.review_min": "2"}, {}),
            saved_by="   ",
            repository=repository,
        )


def test_a_save_that_changes_nothing_is_refused(repository: Repository) -> None:
    _seeded(repository)
    active = config_service.active(ConfigKind.POLICY, repository=repository)

    with pytest.raises(ConfigInvalid, match="Nothing changed"):
        config_service.save(
            ConfigKind.POLICY, active.payload, saved_by="Ada Lovelace", repository=repository
        )


def test_a_policy_that_would_not_validate_is_refused_with_a_sentence(
    repository: Repository,
) -> None:
    """Weights that do not add up to 1 put the score off the 0-5 scale."""
    _seeded(repository)
    active = config_service.active(ConfigKind.POLICY, repository=repository)

    with pytest.raises(ConfigInvalid) as caught:
        config_service.save(
            ConfigKind.POLICY,
            config_service.apply_policy_form(active.payload, {"weights.domain": "0.9"}, {}),
            saved_by="Ada Lovelace",
            repository=repository,
        )

    assert any("add up to" in problem for problem in caught.value.problems)


def test_an_empty_governing_principle_is_refused(repository: Repository) -> None:
    _seeded(repository)
    active = config_service.active(ConfigKind.PROFILE, repository=repository)

    with pytest.raises(ConfigInvalid, match="cannot be empty"):
        config_service.apply_profile_form(
            active.payload,
            principle="   ",
            mandate_yaml=config_service.mandate_yaml(active.payload),
        )


def test_a_mandate_that_is_not_readable_yaml_is_refused_before_it_is_stored(
    repository: Repository,
) -> None:
    _seeded(repository)
    active = config_service.active(ConfigKind.PROFILE, repository=repository)

    with pytest.raises(ConfigInvalid, match="not readable YAML"):
        config_service.apply_profile_form(
            active.payload,
            principle=active.payload["principle"],
            mandate_yaml="identity: [unclosed",
        )


def test_a_diff_is_shown_against_the_active_version(repository: Repository) -> None:
    _seeded(repository)
    active = config_service.active(ConfigKind.POLICY, repository=repository)

    lines = config_service.diff(
        ConfigKind.POLICY,
        config_service.apply_policy_form(active.payload, {"bands.review_min": "2"}, {}),
        repository=repository,
    )

    assert any(line.startswith("+") and "review_min: 2" in line for line in lines)
    assert config_service.diff(ConfigKind.POLICY, active.payload, repository=repository) == []


# ----------------------------------------------------------------- the freeze


def test_a_rules_change_is_refused_while_the_vocabulary_is_frozen(
    repository: Repository,
) -> None:
    _seeded(repository)
    active = config_service.active(ConfigKind.RULES, repository=repository)
    proposed = config_service.apply_rules_form(
        active.payload,
        {active.payload["rules"][0]["id"]: {"active": False}},
        removed=set(),
    )

    with pytest.raises(ConfigFrozen) as caught:
        config_service.save(
            ConfigKind.RULES, proposed, saved_by="Ada Lovelace", repository=repository
        )

    assert "frozen at version 3" in str(caught.value)
    assert "override" in str(caught.value)
    assert repository.active_config(ConfigKind.RULES).version == active.version


def test_the_freeze_lifts_when_the_override_is_set_explicitly(repository: Repository) -> None:
    _seeded(repository)
    active = config_service.active(ConfigKind.RULES, repository=repository)
    first = active.payload["rules"][0]["id"]

    saved = config_service.save(
        ConfigKind.RULES,
        config_service.apply_rules_form(active.payload, {first: {"active": False}}, removed=set()),
        saved_by="Ada Lovelace",
        note="high-strength false positive, the H2 document-label case",
        override_freeze=True,
        repository=repository,
    )

    assert saved.version == active.version + 1
    rules = {rule["id"]: rule for rule in saved.payload["rules"]}
    assert rules[first]["active"] is False


def test_a_duplicate_rule_id_is_refused(repository: Repository) -> None:
    _seeded(repository)
    active = config_service.active(ConfigKind.RULES, repository=repository)
    first = active.payload["rules"][0]

    with pytest.raises(ConfigInvalid) as caught:
        config_service.save(
            ConfigKind.RULES,
            config_service.apply_rules_form(
                active.payload,
                {},
                removed=set(),
                added={**first, "id": first["id"]},
            ),
            saved_by="Ada Lovelace",
            override_freeze=True,
            repository=repository,
        )

    assert any("more than once" in problem for problem in caught.value.problems)


def test_importing_from_a_file_is_refused_by_the_freeze_too(
    repository: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An import is a save whose payload came from a file, not a way round the check."""
    _seeded(repository)
    active = config_service.active(ConfigKind.RULES, repository=repository)

    edited = tmp_path / "rules.yaml"
    payload = {**active.payload, "rules": active.payload["rules"][:-1]}
    edited.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    monkeypatch.setitem(config_service.SEED_PATHS, ConfigKind.RULES, edited)

    with pytest.raises(ConfigFrozen):
        config_service.import_from_files(
            saved_by="Ada Lovelace", repository=repository, kinds=(ConfigKind.RULES,)
        )


def test_export_writes_the_active_versions_back_to_files(
    repository: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seeded(repository)
    target = tmp_path / "policy.yaml"
    monkeypatch.setitem(config_service.SEED_PATHS, ConfigKind.POLICY, target)

    written = config_service.export_to_files(repository=repository, kinds=(ConfigKind.POLICY,))

    assert [kind for kind, _, _ in written] == [ConfigKind.POLICY]
    reloaded = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert reloaded == repository.active_config(ConfigKind.POLICY).payload
    assert "WRITTEN BY" in target.read_text(encoding="utf-8")


# ------------------------------------------------------------------ staleness


def test_staleness_counts_the_latest_result_per_notice_only(
    repository: Repository, make_tender
) -> None:
    """Results are append-only. Counting the superseded ones would never reach zero.

    A number that can never reach zero is one nobody reads after the first week.
    """
    tender = make_tender()
    repository.upsert_tenders([tender], run_id=RUN)

    _screened(repository, tender, rules_version="2", created_at=datetime(2026, 3, 1, tzinfo=UTC))
    _screened(repository, tender, rules_version="3", created_at=datetime(2026, 3, 2, tzinfo=UTC))

    report = repository.screening_staleness(
        ScreeningVersions(
            rules_version="3", policy_version="1", profile_version="1", prompt_version=None
        ),
        "disabled",
        None,
    )

    assert report.total == 0, "the superseded result on version 2 is not counted"


def test_staleness_names_the_reason_a_notice_would_be_screened_again(
    repository: Repository, make_tender
) -> None:
    older = make_tender(source_id="1-2026")
    never = make_tender(source_id="2-2026")
    repository.upsert_tenders([older, never], run_id=RUN)
    _screened(repository, older, rules_version="2", created_at=datetime(2026, 3, 1, tzinfo=UTC))

    report = repository.screening_staleness(
        ScreeningVersions(
            rules_version="3", policy_version="1", profile_version="1", prompt_version=None
        ),
        "disabled",
        None,
    )

    assert report.older_rules == 1
    assert report.never_screened == 1
    assert report.total == 2
    assert ("Screened under an older rule set", 1) in report.lines()


def test_a_saved_configuration_shows_up_in_the_staleness_count(
    repository: Repository, make_tender
) -> None:
    _seeded(repository)
    tender = make_tender()
    repository.upsert_tenders([tender], run_id=RUN)

    before = config_service.snapshot(repository=repository)
    repository.save_screening_result(
        ScreeningResult(
            tender_id=tender.id,
            score=3,
            band=Band.REVIEW,
            rules_only_score=2,
            confidence=0.5,
            rules=RulesResult(
                tender_id=tender.id,
                matches=[],
                domains_hit=[],
                route=RulesRoute.ASSESS,
                rules_version=before.rules.rules_version,
            ),
            explanation="Keyword evidence only.",
            screened_content_hash=tender.content_hash,
            provider="disabled",
            rules_version=before.rules.rules_version,
            policy_version=before.policy.policy_version,
            profile_version=before.profile.profile_version,
            created_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
    )

    assert repository.screening_staleness(before.versions(), "disabled", None).total == 0

    active = config_service.active(ConfigKind.POLICY, repository=repository)
    saved = config_service.save(
        ConfigKind.POLICY,
        config_service.apply_policy_form(active.payload, {"bands.review_min": "2"}, {}),
        saved_by="Ada Lovelace",
        repository=repository,
    )

    after = config_service.snapshot(repository=repository)
    report = repository.screening_staleness(after.versions(), "disabled", None)

    assert after.policy.policy_version == str(saved.version)
    assert report.older_policy == 1
    assert report.total == 1


def test_a_snapshot_is_fixed_and_a_save_landing_later_does_not_reach_it(
    repository: Repository,
) -> None:
    """A run takes one snapshot and holds it to the end.

    A run screens thousands of notices over many minutes. If a save halfway
    through changed what the rest of the run answered to, half the results would
    be stamped with one version and half with another, and nothing on screen would
    say where the line fell.
    """
    _seeded(repository)
    held = config_service.snapshot(repository=repository)
    before = held.policy.policy_version

    active = config_service.active(ConfigKind.POLICY, repository=repository)
    config_service.save(
        ConfigKind.POLICY,
        config_service.apply_policy_form(active.payload, {"bands.review_min": "2"}, {}),
        saved_by="Ada Lovelace",
        repository=repository,
    )

    assert held.policy.policy_version == before
    assert config_service.snapshot(repository=repository).policy.policy_version != before


# -------------------------------------------------------------------- the page


def test_the_settings_page_names_the_freeze_and_the_active_versions(
    client: TestClient, repository: Repository
) -> None:
    html = client.get("/settings").text

    assert "The keyword vocabulary is frozen at version 3" in html
    assert "Keyword rules" in html
    assert "Scoring policy" in html
    assert "Mandate" in html


def test_the_settings_page_refuses_a_rules_save_and_says_why(
    client: TestClient, repository: Repository
) -> None:
    _seeded(repository)
    active = config_service.active(ConfigKind.RULES, repository=repository)
    first = active.payload["rules"][0]["id"]
    client.post("/register/reviewer", data={"name": "Ada Lovelace"})

    response = client.post(
        "/settings/rules",
        data={
            f"rule.{first}.present": "1",
            f"rule.{first}.active": "1",
            f"rule.{first}.aliases": "hydrogen\nwaterstof",
            "note": "trying it",
        },
    )

    assert response.status_code == 400
    assert "frozen" in response.text
    assert repository.active_config(ConfigKind.RULES).version == active.version


def test_an_unknown_configuration_is_a_sentence_not_a_stack_trace(client: TestClient) -> None:
    response = client.get("/settings/nonsense")

    assert response.status_code == 404
    assert "is not a configuration Watchdog holds" in response.text


def test_the_staleness_fragment_loads_on_its_own(
    client: TestClient, repository: Repository
) -> None:
    response = client.get("/settings/staleness")

    assert response.status_code == 200
    assert "What needs screening again" in response.text
