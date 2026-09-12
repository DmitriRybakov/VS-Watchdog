"""The answer cache: a hit costs nothing, and a broken entry is only a miss."""

from __future__ import annotations

from pathlib import Path

from watchdog.llm.cache import ResponseCache, cache_key
from watchdog.llm.provider import Usage


def test_a_stored_answer_comes_back_marked_as_cached(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    key = cache_key(provider="fake", model="fake-1", prompt_version="assess_v1", payload="notice")

    cache.put(key, content='{"ok": true}', usage=Usage(tokens_in=10, tokens_out=5))
    hit = cache.get(key)

    assert hit is not None
    assert hit.content == '{"ok": true}'
    assert hit.usage.cached is True
    assert hit.usage.tokens_in == 10
    assert hit.usage.billable_tokens_in == 0


def test_the_key_changes_when_anything_that_decided_the_answer_changes() -> None:
    base = {
        "provider": "fake",
        "model": "fake-1",
        "prompt_version": "assess_v1",
        "payload": "notice",
    }
    keys = {
        cache_key(**base),
        cache_key(**{**base, "provider": "azure_openai"}),
        cache_key(**{**base, "model": "gpt-4o"}),
        cache_key(**{**base, "prompt_version": "assess_v2"}),
        cache_key(**{**base, "payload": "a corrected notice"}),
    }

    assert len(keys) == 5


def test_a_disabled_cache_never_hits_and_never_writes(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path, enabled=False)
    key = "abc123"

    cache.put(key, content='{"ok": true}', usage=Usage())

    assert cache.get(key) is None
    assert not any(tmp_path.iterdir())


def test_an_unreadable_entry_is_a_miss_rather_than_a_failure(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    key = "deadbeef"
    path = cache.path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("this is not json", encoding="utf-8")

    assert cache.get(key) is None


def test_a_missing_entry_is_a_miss(tmp_path: Path) -> None:
    assert ResponseCache(tmp_path).get("nothing-stored-here") is None
