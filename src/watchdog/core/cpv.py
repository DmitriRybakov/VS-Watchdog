"""CPV codes: normalising them, and matching them the way TED itself does.

Two things here are easy to get wrong and expensive to get wrong.

**Leading zeros.** ``09310000`` is electricity. Parsed as a number it becomes
``9310000``, which is seven characters and matches nothing ever again. Codes are
strings here, always, and a value that arrives as an integer is padded back.

**Hierarchy.** TED's own filter matches descendants: a search for ``71318000``
returns a notice tagged only ``71318100``. Verified against notice 597239-2026,
which carries ``71318100`` and no other 713 code, and which TED returns for
``71318000``, ``71310000`` and ``71000000`` but not for ``45000000``.

``startswith`` on the full code does not reproduce that, because the configured
code is padded with the zeros that make it a parent. The significant prefix is
the code with its trailing zeros removed - ``71318000`` becomes ``71318`` - and a
code matches when it starts with that prefix.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

CPV_LENGTH = 8

# Two digits is a CPV division, the coarsest real level. Without this floor,
# 70000000 would strip to "7" and match everything from 70 to 79.
MIN_PREFIX_LENGTH = 2

# The lowest CPV division is 03, so a code can lose at most one leading zero on
# its way through a YAML integer. Anything shorter is a truncated code, not a
# padded one, and padding it would invent a different code entirely.
MIN_DIGITS = CPV_LENGTH - 1


def normalise_cpv(value: str | int | None) -> str | None:
    """Return an eight-digit CPV string, or None if it is not one.

    Accepts the integer PyYAML produces for an unquoted code, and the
    ``12345678-9`` form some sources use, whose suffix is a check digit.
    """
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    text = text.split("-", 1)[0].strip()
    if not text.isdigit():
        return None

    if not MIN_DIGITS <= len(text) <= CPV_LENGTH:
        return None

    return text.zfill(CPV_LENGTH)


def significant_prefix(code: str) -> str:
    """The configured code with its padding zeros removed, floored at a division."""
    trimmed = code.rstrip("0")
    if len(trimmed) < MIN_PREFIX_LENGTH:
        trimmed = code[:MIN_PREFIX_LENGTH]
    return trimmed


def cpv_matches(configured: str | int, actual: str | int) -> bool:
    """True when ``actual`` is ``configured`` or any code beneath it."""
    wanted = normalise_cpv(configured)
    candidate = normalise_cpv(actual)
    if wanted is None or candidate is None:
        return False
    return candidate.startswith(significant_prefix(wanted))


def matched_cpv_codes(configured: Sequence[str | int], actual: Iterable[str | int]) -> list[str]:
    """Every code in ``actual`` that falls under one of the configured codes.

    Returns the codes themselves rather than a yes or no, because a score has to
    be able to say which code it matched on.
    """
    prefixes = [significant_prefix(code) for code in _normalised(configured)]
    if not prefixes:
        return []

    return [
        code for code in _normalised(actual) if any(code.startswith(prefix) for prefix in prefixes)
    ]


def _normalised(values: Iterable[str | int]) -> list[str]:
    """Normalise a sequence, dropping anything that is not a CPV code, keeping order."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        code = normalise_cpv(value)
        if code is not None and code not in seen:
            seen.add(code)
            out.append(code)
    return out


def normalise_cpv_list(values: Iterable[str | int]) -> list[str]:
    """Deduplicate and normalise, preserving the order the source gave them in."""
    return _normalised(values)
