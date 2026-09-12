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
from watchdog.screening.policy import (
    Decision,
    PolicyConfig,
    RulesEvidence,
    decide,
    load_policy,
    parse_policy,
    ranking_key,
    rules_only_grade,
)
from watchdog.screening.profile import Profile, load_profile, parse_profile
from watchdog.screening.prompts import DEFAULT_PROMPT_VERSION, render_system_prompt
from watchdog.screening.rules import RuleEngine, normalise

__all__ = [
    "DEFAULT_PROMPT_VERSION",
    "AssessRun",
    "AssessmentOutcome",
    "Candidate",
    "Decision",
    "PolicyConfig",
    "Profile",
    "Rule",
    "RuleEngine",
    "RulesConfig",
    "RulesEvidence",
    "assess",
    "build_notice_text",
    "decide",
    "load_policy",
    "load_profile",
    "load_rules_config",
    "next_version",
    "normalise",
    "parse_policy",
    "parse_profile",
    "parse_rules_config",
    "ranking_key",
    "render_system_prompt",
    "rules_only_grade",
    "save_rules_config",
]
