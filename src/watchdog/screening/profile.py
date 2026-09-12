"""The shape of config/profile.yaml, and the one part of it a model ever sees.

The file has two parts and they are used differently. Part 1, ``screening_mandate``,
together with the governing principle, is injected verbatim into every assessment.
Part 2, ``commercial_reality``, is not - geography, contract value, language and
bid route change the bid route, never the relevance score, and most of them can
only be judged from the tender documents anyway.

That separation is enforced structurally rather than by remembering: this model
declares only the fields Part 1 needs and ignores everything else, so
``commercial_reality`` is dropped at the door and cannot reach a prompt even if
someone later builds one from the whole object.

Every screening result records ``profile_version``. A change to Part 1 is a bump,
and a bump marks earlier results stale.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_PROFILE_PATH = Path("config/profile.yaml")

# Keys of Part 1, in the order a person reads them. Anything else in the mandate
# block is still sent - the file is owned by Entr, not by this code - but these
# come first so the model reads the identity and the domain levels before the rest.
_MANDATE_ORDER = ("identity", "domain", "services", "decision_stage", "edge_cases")


class Profile(BaseModel):
    """Part 1 of the screening profile: what we are for, and what we are not.

    ``extra="ignore"`` is the guarantee, not a convenience: Part 2 never becomes
    an attribute of this object, so it can never be serialised into a prompt.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    version: int = Field(ge=1)
    updated: date | None = None
    owner: str | None = None
    # The governing principle: relevance and bidability are judged separately.
    principle: str
    screening_mandate: dict[str, Any] = Field(default_factory=dict)

    @field_validator("principle")
    @classmethod
    def _require_principle(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError(
                "config/profile.yaml has no `principle`; it is what stops geography "
                "and contract value reaching the relevance score"
            )
        return text

    @property
    def profile_version(self) -> str:
        """The version as it is recorded on a screening result."""
        return str(self.version)

    def mandate_text(self) -> str:
        """Part 1 as the text a model is given. Part 2 is not in this object."""
        mandate = self.screening_mandate
        ordered = {key: mandate[key] for key in _MANDATE_ORDER if key in mandate}
        ordered.update({key: value for key, value in mandate.items() if key not in ordered})

        document = {
            "governing_principle": self.principle,
            "screening_mandate": ordered,
        }
        rendered: str = yaml.safe_dump(
            document,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
            width=96,
        )
        return rendered.strip()


def parse_profile(data: dict[str, Any]) -> Profile:
    """Validate an already-loaded mapping. Pure, so it is easy to test."""
    return Profile.model_validate(data or {})


def load_profile(path: Path | str = DEFAULT_PROFILE_PATH) -> Profile:
    """Read the seed file. Callers are the service layer only."""
    text = Path(path).read_text(encoding="utf-8")
    return parse_profile(yaml.safe_load(text) or {})
