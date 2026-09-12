"""The Azure provider's decisions that can be checked without a key.

The call itself cannot be exercised until a deployment exists, so what is tested
here is everything that decides what gets sent and what a failure reads like:
which models take the sampling settings, the narrowed schema, and whether a
rejection names the part that has to change.
"""

from __future__ import annotations

import pytest

from watchdog.core.models import Assessment
from watchdog.llm.azure_openai import (
    SAMPLING_SETTINGS,
    _provider_schema,
    _sampling_wanted,
    diagnose,
    rejected_setting,
    supports_sampling,
)


class _ApiError(Exception):
    """Stands in for the SDK's error, which carries a status and a message."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-4o", True),
        ("gpt-4o-mini", True),
        ("entr-gpt-4o-prod", True),
        ("o1", False),
        ("o3-mini", False),
        ("entr-o3-mini-prod", False),
        ("gpt-5", False),
        ("gpt-5-chat", False),
    ],
)
def test_which_models_take_the_sampling_settings(model: str, expected: bool) -> None:
    assert supports_sampling(model) is expected


def test_the_setting_can_override_the_model_name() -> None:
    assert _sampling_wanted("o3-mini", "on") is True
    assert _sampling_wanted("gpt-4o", "off") is False
    assert _sampling_wanted("gpt-4o", "auto") is True


def test_a_rejected_sampling_setting_is_recognised_and_named() -> None:
    exc = _ApiError("Unsupported parameter: 'temperature' is not supported with this model.")

    assert rejected_setting(exc) == "temperature"


def test_an_ordinary_failure_is_not_read_as_a_sampling_problem() -> None:
    assert rejected_setting(_ApiError("Internal server error", status_code=500)) is None


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_ApiError("Access denied", status_code=401), "LLM_API_KEY"),
        (_ApiError("DeploymentNotFound", status_code=404), "deployment name"),
        (_ApiError("Requests to the endpoint exceeded", status_code=429), "rate limited"),
        (_ApiError("Invalid schema for response_format: 'maxLength' is not permitted"), "schema"),
        (_ApiError("Unsupported parameter: 'seed'"), "LLM_SAMPLING=off"),
        (_ApiError("Request timed out"), "LLM_TIMEOUT_SECONDS"),
    ],
)
def test_a_failure_names_the_part_that_was_rejected(exc: Exception, expected: str) -> None:
    explained = diagnose(exc, model="entr-gpt-4o")

    assert expected in explained
    # The vendor's own wording is kept: two failures can read alike without it.
    assert str(exc) in explained


def test_an_unrecognised_failure_still_carries_the_vendor_message() -> None:
    explained = diagnose(_ApiError("something new"), model="entr-gpt-4o")

    assert "The model rejected the request." in explained
    assert "something new" in explained


def test_the_sampling_settings_are_the_three_that_make_a_run_repeatable() -> None:
    assert SAMPLING_SETTINGS == {"temperature": 0, "top_p": 1, "seed": 20260912}


def test_the_schema_drops_the_keywords_strict_mode_rejects() -> None:
    schema = _provider_schema(Assessment)

    text = repr(schema)
    for keyword in ("maxLength", "minimum", "maximum", "default"):
        assert keyword not in text


def test_every_object_in_the_schema_is_closed_and_fully_required() -> None:
    schema = _provider_schema(Assessment)

    def walk(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        if node.get("type") == "object" and "properties" in node:
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
        for value in node.values():
            walk(value)

    walk(schema)
