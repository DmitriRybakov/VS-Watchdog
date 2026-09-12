"""Versioned prompt templates, and what gets filled into them.

A prompt is data, not code: it lives in a ``.md`` file next to this module and is
named by version. Every screening result records ``prompt_version``, so a change
to the wording marks earlier results stale rather than silently rewriting how a
past score was reached. That only works if a template is never edited in place -
add ``assess_v2.md`` instead.

Two things are filled in rather than written out in the template:

- the mandate, which is Part 1 of ``config/profile.yaml`` and is owned by Entr;
- the label vocabularies, which come from ``core.enums``. Writing them into the
  template by hand would let the prompt and the code drift apart, and the symptom
  would be a model returning a label that fails validation on every notice.
"""

from __future__ import annotations

import re
from importlib import resources

from watchdog.core.enums import DecisionStage, Domain, ServiceType

DEFAULT_PROMPT_VERSION = "assess_v1"

_PACKAGE = "watchdog.screening.prompts"
_TEMPLATE_SUFFIX = ".md"

# Maintainer notes in the template. Stripped so they are not paid for on every notice.
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


class PromptNotFound(LookupError):
    """No template file for that version. A typo, or a version never committed."""


def available_versions() -> list[str]:
    """Every prompt version shipped with this build, in order."""
    return sorted(
        entry.name[: -len(_TEMPLATE_SUFFIX)]
        for entry in resources.files(_PACKAGE).iterdir()
        if entry.name.endswith(_TEMPLATE_SUFFIX)
    )


def load_template(version: str = DEFAULT_PROMPT_VERSION) -> str:
    """The raw template, placeholders and all."""
    if not re.fullmatch(r"[a-z0-9_]+", version):
        raise PromptNotFound(f"{version!r} is not a prompt version name")

    try:
        text = (
            resources.files(_PACKAGE)
            .joinpath(f"{version}{_TEMPLATE_SUFFIX}")
            .read_text(encoding="utf-8")
        )
    except OSError as exc:
        known = ", ".join(available_versions()) or "none"
        raise PromptNotFound(
            f"there is no prompt version {version!r}; the ones shipped are: {known}"
        ) from exc

    return text


def render_system_prompt(*, mandate: str, version: str = DEFAULT_PROMPT_VERSION) -> str:
    """The system prompt as it is actually sent, with nothing left to fill in."""
    rendered = load_template(version)
    for placeholder, value in (
        ("{{MANDATE}}", mandate.strip()),
        ("{{DOMAIN_LABELS}}", _labels(Domain)),
        ("{{SERVICE_LABELS}}", _labels(ServiceType)),
        ("{{STAGE_LABELS}}", _labels(DecisionStage)),
    ):
        rendered = rendered.replace(placeholder, value)

    rendered = _HTML_COMMENT.sub("", rendered).strip()

    left = re.findall(r"\{\{[A-Z_]+\}\}", rendered)
    if left:
        raise PromptNotFound(
            f"prompt {version!r} has placeholders nothing fills in: {', '.join(sorted(set(left)))}"
        )

    return rendered


def _labels(vocabulary: type[Domain] | type[ServiceType] | type[DecisionStage]) -> str:
    return ", ".join(member.value for member in vocabulary)
