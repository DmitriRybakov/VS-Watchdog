"""config/profile.yaml: Part 1 is loaded, Part 2 cannot be."""

from __future__ import annotations

from pathlib import Path

import pytest

from watchdog.screening.profile import Profile, load_profile, parse_profile

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def test_the_shipped_profile_loads() -> None:
    profile = load_profile(CONFIG_DIR / "profile.yaml")

    assert profile.version >= 1
    assert profile.profile_version == str(profile.version)
    assert "screening_mandate" in profile.mandate_text()


def test_part_two_never_becomes_an_attribute() -> None:
    profile = parse_profile(
        {
            "version": 1,
            "principle": "Relevance and bidability are assessed separately.",
            "screening_mandate": {"identity": "Entr"},
            "commercial_reality": {"geography": {"priority": "Nordics"}},
        }
    )

    assert not hasattr(profile, "commercial_reality")
    assert "Nordics" not in profile.mandate_text()


def test_a_profile_without_the_governing_principle_is_refused() -> None:
    with pytest.raises(ValueError, match="principle"):
        parse_profile({"version": 1, "principle": "   ", "screening_mandate": {}})


def test_the_mandate_reads_identity_before_the_rest() -> None:
    profile = Profile(
        version=1,
        principle="Relevance and bidability are assessed separately.",
        screening_mandate={"decision_stage": "later", "identity": "first"},
    )

    text = profile.mandate_text()

    assert text.index("identity") < text.index("decision_stage")
