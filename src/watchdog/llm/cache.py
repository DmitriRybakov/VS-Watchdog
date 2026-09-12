"""A content-addressed cache of model answers.

The key is the whole of what decided the answer: provider, model, prompt version
and the exact text sent. Change any of them and the key changes, so a stale
answer can never be served for a corrected notice or an edited prompt - the same
reasoning as ``Tender.content_hash``.

A hit costs nothing, which is what makes a re-run and a demo instant and free.
The tokens on a hit are the ones the original call spent; they are reported for
information and never added to a run's total a second time.

This lives under ``data/`` and is disposable. Deleting it loses nothing but money:
every answer that matters is stored in the database. Nothing here raises - a cache
that cannot be read is a cache miss, never a failed screening.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from watchdog.core.clock import utc_now
from watchdog.core.logging import get_logger
from watchdog.llm.provider import Usage

log = get_logger(__name__)

CACHE_DIR_NAME = "llm-cache"

# Same separator as the content hash, for the same reason: it cannot occur in any
# of the parts, so two different inputs cannot be run together into one key.
_KEY_SEPARATOR = "\x1f"


def cache_key(*, provider: str, model: str | None, prompt_version: str, payload: str) -> str:
    """The address of one answer. Everything that decided it is in here."""
    parts = [provider, model or "", prompt_version, payload]
    return hashlib.sha256(_KEY_SEPARATOR.join(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CachedAnswer:
    """A stored answer, exactly as the model returned it, with what it cost then."""

    content: str
    usage: Usage


class ResponseCache:
    """Answers on disk, one file per key.

    ``enabled=False`` is a working cache that never hits and never writes, so
    ``--no-cache`` needs no branch at the call site.
    """

    def __init__(self, directory: Path, *, enabled: bool = True) -> None:
        self.directory = directory
        self.enabled = enabled

    @classmethod
    def under(cls, data_dir: Path, *, enabled: bool = True) -> ResponseCache:
        return cls(Path(data_dir) / CACHE_DIR_NAME, enabled=enabled)

    def path_for(self, key: str) -> Path:
        # Two levels, so a large cache does not become one directory with
        # a hundred thousand files in it.
        return self.directory / key[:2] / f"{key}.json"

    def get(self, key: str) -> CachedAnswer | None:
        if not self.enabled:
            return None

        path = self.path_for(key)
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            content = stored["content"]
            usage = Usage.model_validate(stored.get("usage") or {})
        except (OSError, ValueError, KeyError, TypeError):
            # Unreadable, half-written or written by an older version. A miss.
            return None

        if not isinstance(content, str):
            return None

        return CachedAnswer(content=content, usage=usage.model_copy(update={"cached": True}))

    def put(self, key: str, *, content: str, usage: Usage) -> None:
        if not self.enabled:
            return

        path = self.path_for(key)
        record = {
            "content": content,
            "usage": usage.model_copy(update={"cached": False}).model_dump(mode="json"),
            "stored_at": utc_now().isoformat(),
        }

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Written beside the target and renamed, so an interrupted write
            # cannot leave half an answer where a whole one is expected.
            handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{key}.", suffix=".tmp")
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    json.dump(record, stream)
                os.replace(temporary, path)
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise
        except OSError as exc:
            # A cache that cannot be written is a slower run, not a failed one.
            log.warning("llm_cache_write_failed", error=str(exc))
