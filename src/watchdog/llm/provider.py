"""The gateway every language-model call goes through.

One rule holds this package together: a model SDK is imported inside its own
implementation module and nowhere else. Everything above this line - services,
screening, the web layer - sees only :class:`LLMProvider`, which knows nothing
about any vendor.

Two behaviours live here rather than in each implementation, because a provider
that validated differently from the others would be a provider whose answers
cannot be compared with the others':

- **Validation is ours.** Whatever a vendor calls its structured-output mode, the
  answer is validated against the pydantic schema on this side. The vendor's own
  guarantee is a helpful narrowing, never the contract.
- **One repair attempt, then stop.** An answer that fails validation is sent back
  once with the validation error appended. If the second answer also fails, that
  is :class:`LLMOutputError` and the notice is left unassessed for a later retry.
  A loop here would burn tokens on a model that has misread the schema.

``disabled`` is the default provider and the whole application must work under
it. Callers handle :class:`ProviderDisabled` by producing a rules-only result;
they never let it reach a colleague as an error.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, Field, ValidationError

if TYPE_CHECKING:
    from watchdog.core.settings import Settings

# The provider name that means "no model is involved". Stored on results, so it
# must never change once rows exist.
DISABLED = "disabled"

# Separator used when hashing prompt parts, so a system prompt ending in "a" and
# a user prompt starting with "b" cannot hash the same as the other way round.
_HASH_SEPARATOR = "\x1f"

# Appended to the user message on the one repair attempt. It names what was wrong
# and repeats the contract. It does not read the rejected answer back in full; the
# one exception is a quote that was not in the notice, which has to be named or the
# model cannot know which phrase to stop inventing.
REPAIR_INSTRUCTION = (
    "Your previous answer was rejected. Answer again. Return one JSON object and nothing "
    "else - no prose, no markdown, no code fence. Copy every evidence quote character for "
    "character from the notice text above. These are the problems with the answer:"
)


class Usage(BaseModel):
    """What one assessment cost, kept whole so a run's total can be explained.

    ``attempts`` is 2 when a repair was needed. ``cached`` means the answer came
    from a previous identical call, so it cost nothing this time - the tokens are
    the ones the original call spent and must not be added to a run's total again.
    """

    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    attempts: int = Field(default=1, ge=0)
    cached: bool = False

    @property
    def repaired(self) -> bool:
        return self.attempts > 1

    @property
    def billable_tokens_in(self) -> int:
        return 0 if self.cached else self.tokens_in

    @property
    def billable_tokens_out(self) -> int:
        return 0 if self.cached else self.tokens_out


class LLMError(RuntimeError):
    """Base class for everything this package raises."""


class ProviderDisabled(LLMError):
    """No model is configured. Expected, not exceptional.

    Every caller handles this by producing a rules-only result and saying on
    screen that AI assessment is off.
    """

    def __init__(self, message: str = "AI assessment is switched off") -> None:
        super().__init__(message)


class ProviderNotConfigured(LLMError):
    """A provider was named but cannot be built: unknown name, or missing settings."""


class LLMCallError(LLMError):
    """The provider could not be reached, or answered with an error.

    Retrying later is reasonable; the notice is left unassessed, never archived.
    """


class LLMOutputError(LLMError):
    """The answer did not match the schema, and neither did the repair attempt."""

    def __init__(self, message: str, *, errors: str, attempts: int) -> None:
        super().__init__(message)
        self.errors = errors
        self.attempts = attempts


class LLMProvider(Protocol):
    """What the rest of Watchdog is allowed to know about a language model."""

    name: str
    model: str | None
    # False for the disabled provider. Callers check this before building prompts
    # so that the switched-off path costs nothing at all.
    enabled: bool

    def assess[SchemaT: BaseModel](
        self,
        *,
        system: str,
        user: str,
        schema: type[SchemaT],
        check: Callable[[SchemaT], None] | None = None,
    ) -> tuple[SchemaT, Usage]:
        """Ask the model and return a validated answer with what it cost.

        ``check`` is validation the schema cannot express because it depends on
        what was sent - that every quote occurs in the notice, for instance. It
        raises ``ValueError`` to reject an answer, and a rejection here is repaired
        exactly like a schema failure.
        """
        ...


# Sends one pair of messages and returns the raw answer text with what it cost.
# The one thing a provider implementation has to supply.
RawCall = Callable[[str, str], tuple[str, Usage]]


def input_hash(system: str, user: str) -> str:
    """Fingerprint of exactly what was sent, so an answer can be tied to its input."""
    return hashlib.sha256(_HASH_SEPARATOR.join([system, user]).encode("utf-8")).hexdigest()


def timed(call: Callable[[], tuple[str, Usage]]) -> tuple[str, Usage]:
    """Run ``call`` and fill in how long it waited, in milliseconds."""
    started = time.perf_counter()
    raw, usage = call()
    elapsed = int((time.perf_counter() - started) * 1000)
    return raw, usage.model_copy(update={"latency_ms": elapsed})


def parse_with_repair[SchemaT: BaseModel](
    send: RawCall,
    *,
    system: str,
    user: str,
    schema: type[SchemaT],
    check: Callable[[SchemaT], None] | None = None,
) -> tuple[SchemaT, Usage]:
    """Validate the answer; on failure ask once more, then give up.

    Shared by every real provider so that "what counts as a valid answer" is one
    decision rather than one per vendor.
    """
    raw, first_usage = send(system, user)

    try:
        return _validated(raw, schema, check), first_usage
    except ValueError as first_error:
        # Summarised inside the handler: Python deletes the bound name on the way out.
        first_errors = _error_summary(first_error)

    repair_user = f"{user}\n\n{REPAIR_INSTRUCTION}\n{first_errors}"
    repaired_raw, second_usage = send(system, repair_user)
    usage = _combine(first_usage, second_usage)

    try:
        return _validated(repaired_raw, schema, check), usage
    except ValueError as second_error:
        raise LLMOutputError(
            f"the model's answer did not match {schema.__name__} after one repair attempt",
            errors=_error_summary(second_error),
            attempts=usage.attempts,
        ) from second_error


def get_provider(settings: Settings | None = None) -> LLMProvider:
    """Build the configured provider. ``disabled`` when nothing is configured."""
    # Imported here, not at module scope: each implementation imports this module
    # for Usage and the error types, so importing them at the top would be a cycle.
    from watchdog.core.settings import get_settings
    from watchdog.llm.disabled import DisabledProvider
    from watchdog.llm.fake import FakeProvider

    resolved = settings or get_settings()
    name = resolved.llm_provider.strip().lower()

    if name in {"", DISABLED, "off", "none"}:
        return DisabledProvider()
    if name == "fake":
        return FakeProvider(model=resolved.llm_model or "fake-1")
    if name in {"azure", "azure_openai"}:
        from watchdog.llm.azure_openai import AzureOpenAIProvider

        return AzureOpenAIProvider.from_settings(resolved)

    raise ProviderNotConfigured(
        f"there is no model provider called {name!r}; "
        f"set LLM_PROVIDER to one of: {DISABLED}, fake, azure_openai"
    )


def _combine(first: Usage, second: Usage) -> Usage:
    """The cost of an answer that needed a repair: both attempts, added up."""
    return Usage(
        tokens_in=first.tokens_in + second.tokens_in,
        tokens_out=first.tokens_out + second.tokens_out,
        latency_ms=first.latency_ms + second.latency_ms,
        attempts=first.attempts + second.attempts,
        cached=False,
    )


def _validated[SchemaT: BaseModel](
    raw: str, schema: type[SchemaT], check: Callable[[SchemaT], None] | None
) -> SchemaT:
    value = schema.model_validate_json(raw)
    if check is not None:
        check(value)
    return value


def _error_summary(error: ValueError) -> str:
    """The failures as short lines. Never the rejected answer, beyond what a check names."""
    if not isinstance(error, ValidationError):
        return f"- {error}"

    lines = []
    for item in error.errors(include_url=False, include_input=False):
        location = ".".join(str(part) for part in item["loc"]) or "(whole answer)"
        lines.append(f"- {location}: {item['msg']}")
    return "\n".join(lines)
