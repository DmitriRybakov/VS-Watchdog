"""Screening engine: deterministic rules, axis scores, bands.

Imports core only - never sources. Policy lives in docs/SCREENING_POLICY.md and
config/policy.yaml, never in this code.
"""

from watchdog.screening.assess import (
    AssessmentOutcome,
    AssessRun,
    Candidate,
    assess,
    build_notice_text,
)
from watchdog.screening.config import (
    Rule,
    RulesConfig,
    load_rules_config,
    next_version,
    parse_rules_config,
    save_rules_config,
)
from watchdog.screening.profile import Profile, load_profile, parse_profile
from watchdog.screening.prompts import DEFAULT_PROMPT_VERSION, render_system_prompt
from watchdog.screening.rules import RuleEngine, normalise

__all__ = [
    "DEFAULT_PROMPT_VERSION",
    "AssessRun",
    "AssessmentOutcome",
    "Candidate",
    "Profile",
    "Rule",
    "RuleEngine",
    "RulesConfig",
    "assess",
    "build_notice_text",
    "load_profile",
    "load_rules_config",
    "next_version",
    "normalise",
    "parse_profile",
    "parse_rules_config",
    "render_system_prompt",
    "save_rules_config",
]
