"""The real provider: Azure OpenAI. The only module allowed to import the SDK.

Four deliberate choices:

- **The SDK is imported inside the constructor**, not at module scope. Watchdog
  installs and runs with no model configured, and importing every module must not
  require a package that only this path needs.
- **Structured output is asked for, not relied on.** The request uses the
  provider's JSON-schema mode, and the answer is still validated against the
  pydantic schema by :func:`parse_with_repair`. The vendor's guarantee narrows the
  failure modes; it does not replace our contract.
- **Low variability where the model allows it.** Temperature 0, top_p 1 and a
  fixed seed, so re-running the same notice gives the same answer as far as the
  service allows. A screening score that moved on its own could not be defended.
  Reasoning models reject those three outright rather than ignoring them, so they
  are sent only where they are supported, and a rejection switches them off for
  the rest of the process instead of failing the run.
- **A rejected call says which part was rejected.** Schema keyword, temperature,
  seed, authentication, deployment name or rate limit, named in a sentence, with
  the vendor's own wording kept after it. None of this can be checked before a key
  exists, so the first live failure has to be readable on its own.

Azure's strict schema mode accepts a smaller JSON Schema dialect than pydantic
emits, so the schema is narrowed on the way out - see :func:`_provider_schema`.
Nothing is lost: the full constraints are still applied when the answer comes
back.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from watchdog.core.logging import get_logger
from watchdog.llm.provider import (
    LLMCallError,
    ProviderNotConfigured,
    Usage,
    parse_with_repair,
    timed,
)

if TYPE_CHECKING:
    from watchdog.core.settings import Settings

log = get_logger(__name__)

# Fixed so that repeating a run gives the same answer where the service supports it.
SEED = 20260912

# The request settings that make a run repeatable, and the ones a reasoning model
# refuses. Sent together, dropped together.
SAMPLING_SETTINGS: dict[str, Any] = {"temperature": 0, "top_p": 1, "seed": SEED}

# Model families that reject the sampling settings rather than ignoring them.
# Matched as whole tokens, because a deployment is usually named after the model
# with something else around it - "entr-o3-mini", "gpt-5-chat".
NO_SAMPLING_FAMILIES = ("o1", "o3", "o4", "o5", "gpt-5")

# Keywords pydantic emits that Azure's strict structured-output mode rejects.
# Dropping them costs nothing: the same constraints are enforced by pydantic when
# the answer is validated on the way back in.
_UNSUPPORTED_KEYWORDS = frozenset(
    {
        "default",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "multipleOf",
        "pattern",
        "uniqueItems",
    }
)


def supports_sampling(model: str) -> bool:
    """False for the model families that refuse temperature, top_p and seed."""
    name = model.casefold()
    return not any(
        re.search(rf"(?<![a-z0-9]){re.escape(family)}(?![a-z0-9])", name)
        for family in NO_SAMPLING_FAMILIES
    )


class AzureOpenAIProvider:
    """Assessment against an Azure OpenAI deployment."""

    name: str = "azure_openai"

    def __init__(
        self,
        *,
        model: str,
        endpoint: str,
        api_key: str,
        api_version: str,
        timeout_seconds: float = 60.0,
        sampling: str = "auto",
    ) -> None:
        try:
            from openai import AzureOpenAI
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise ProviderNotConfigured(
                "the openai package is not installed, so the Azure provider cannot be used; "
                "install it with: pip install entr-watchdog[azure]"
            ) from exc

        self.model: str | None = model
        self.enabled: bool = True
        self.sampling = _sampling_wanted(model, sampling)
        self._client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            timeout=timeout_seconds,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> AzureOpenAIProvider:
        """Build from the environment, naming anything that is missing."""
        model = settings.llm_model
        endpoint = settings.llm_endpoint
        api_key = settings.llm_api_key

        missing = [
            name
            for name, value in (
                ("LLM_MODEL", model),
                ("LLM_ENDPOINT", endpoint),
                ("LLM_API_KEY", api_key),
            )
            if not value
        ]
        if missing or model is None or endpoint is None or api_key is None:
            raise ProviderNotConfigured(
                "the Azure provider needs these environment variables, which are not set: "
                + ", ".join(missing)
            )

        return cls(
            model=model,
            endpoint=endpoint,
            api_key=api_key,
            api_version=settings.llm_api_version,
            timeout_seconds=settings.llm_timeout_seconds,
            sampling=settings.llm_sampling,
        )

    def assess[SchemaT: BaseModel](
        self,
        *,
        system: str,
        user: str,
        schema: type[SchemaT],
        check: Callable[[SchemaT], None] | None = None,
    ) -> tuple[SchemaT, Usage]:
        response_format = _response_format(schema)

        def send(system_text: str, user_text: str) -> tuple[str, Usage]:
            return timed(lambda: self._call(system_text, user_text, response_format))

        return parse_with_repair(send, system=system, user=user, schema=schema, check=check)

    def _call(self, system: str, user: str, response_format: dict[str, Any]) -> tuple[str, Usage]:
        try:
            return self._create(system, user, response_format, sampling=self.sampling)
        except LLMCallError:
            raise
        except Exception as exc:
            rejected = rejected_setting(exc)
            if rejected is None or not self.sampling:
                raise LLMCallError(diagnose(exc, model=self.model)) from exc

        # Said once per process, not once per notice: the model does not change.
        self.sampling = False
        log.warning("llm_sampling_switched_off", model=self.model, rejected=rejected)

        try:
            return self._create(system, user, response_format, sampling=False)
        except LLMCallError:
            raise
        except Exception as exc:
            raise LLMCallError(diagnose(exc, model=self.model)) from exc

    def _create(
        self,
        system: str,
        user: str,
        response_format: dict[str, Any],
        *,
        sampling: bool,
    ) -> tuple[str, Usage]:
        completion = self._client.chat.completions.create(
            model=self.model or "",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format=response_format,
            **(SAMPLING_SETTINGS if sampling else {}),
        )

        choice = completion.choices[0] if completion.choices else None
        content = choice.message.content if choice is not None else None
        if not content:
            reason = getattr(choice, "finish_reason", None) or "no reason given"
            raise LLMCallError(
                f"The model returned an empty answer ({reason}). If the reason is "
                "content_filter, the notice text tripped a filter; if it is length, the "
                "answer was cut off and LLM_NOTICE_CHARS is probably too high."
            )

        usage = completion.usage
        return content, Usage(
            tokens_in=usage.prompt_tokens if usage else 0,
            tokens_out=usage.completion_tokens if usage else 0,
        )


def _sampling_wanted(model: str, setting: str) -> bool:
    """Whether to send temperature, top_p and seed at all.

    ``auto`` asks the model name; ``on`` and ``off`` are for a deployment whose
    name does not say what it is.
    """
    choice = setting.strip().casefold()
    if choice == "on":
        return True
    if choice == "off":
        return False
    return supports_sampling(model)


def rejected_setting(exc: Exception) -> str | None:
    """The sampling setting the service refused, if that is what went wrong.

    Recognised from the message rather than from a status code, because the
    services return a plain 400 for this and name the parameter in the body.
    """
    message = str(exc).casefold()
    if not any(
        phrase in message
        for phrase in ("unsupported", "not supported", "unrecognized", "unknown parameter")
    ):
        return None

    named = [name for name in SAMPLING_SETTINGS if name in message]
    return ", ".join(named) if named else None


def diagnose(exc: Exception, *, model: str | None = None) -> str:
    """Say which part of the request was rejected, then keep the vendor's wording.

    A raw vendor message is a paragraph of JSON that names no action. This leads
    with the part that has to change - and the vendor text stays, because it is
    what distinguishes two failures that look alike.
    """
    message = str(exc)
    lowered = message.casefold()
    status = getattr(exc, "status_code", None)

    if status in {401, 403} or "api key" in lowered or "unauthorized" in lowered:
        named = "The API key was rejected. Check LLM_API_KEY, and that it belongs to LLM_ENDPOINT."
    elif status == 404 or "deploymentnotfound" in lowered.replace(" ", ""):
        named = (
            f"The deployment {model!r} was not found. LLM_MODEL must be the deployment name "
            "in Azure, which is not always the model name, and LLM_ENDPOINT must be its resource."
        )
    elif status == 429 or "rate limit" in lowered or "quota" in lowered:
        named = "The request was rate limited or out of quota. Try again, or lower LLM_CONCURRENCY."
    elif _names_the_schema(lowered):
        named = (
            "The JSON schema was rejected. Strict structured output accepts a narrower dialect "
            "than pydantic emits; the keywords we already strip are _UNSUPPORTED_KEYWORDS in "
            "this module, and whichever keyword the message names belongs in that set."
        )
    elif (rejected := rejected_setting(exc)) is not None:
        named = (
            f"The model rejected {rejected}. Set LLM_SAMPLING=off to stop sending temperature, "
            "top_p and seed to this deployment."
        )
    elif "timed out" in lowered or "timeout" in lowered or "connect" in lowered:
        named = (
            "The model could not be reached. Check the network and LLM_ENDPOINT, "
            "or raise LLM_TIMEOUT_SECONDS."
        )
    else:
        named = "The model rejected the request."

    return f"{named} The service said: {message}"


def _names_the_schema(lowered: str) -> bool:
    return any(
        word in lowered
        for word in ("response_format", "json_schema", "schema", "additionalproperties")
    )


def _response_format(schema: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema.__name__.lower(),
            "strict": True,
            "schema": _provider_schema(schema),
        },
    }


def _provider_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """The pydantic schema in the dialect Azure's strict mode accepts.

    Every object gets ``additionalProperties: false`` and every property listed as
    required, which is what strict mode means; keywords it does not implement are
    dropped rather than sent and rejected.
    """
    narrowed = _narrow(schema.model_json_schema())
    if not isinstance(narrowed, dict):  # pragma: no cover - a schema is always an object
        raise ProviderNotConfigured(f"{schema.__name__} does not describe a JSON object")
    return narrowed


def _narrow(node: Any) -> Any:
    if isinstance(node, list):
        return [_narrow(item) for item in node]
    if not isinstance(node, dict):
        return node

    narrowed: dict[str, Any] = {
        key: _narrow(value) for key, value in node.items() if key not in _UNSUPPORTED_KEYWORDS
    }

    if narrowed.get("type") == "object" and "properties" in narrowed:
        narrowed["additionalProperties"] = False
        narrowed["required"] = list(narrowed["properties"])

    return narrowed
