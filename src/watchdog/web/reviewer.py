"""Who is recording a decision. Attribution, and deliberately not a sign-in.

There is no authentication in Watchdog yet. What there is, is a name typed once
and kept in a cookie, written onto every review made from that browser.

Being clear about what this is worth:

- It is **not** a security control. Nobody's identity is verified, anybody can
  type anybody's name, and no permission anywhere depends on it. It must never be
  presented as a login, and nothing may ever be authorised by it.
- It **is** the difference between a review that can be attributed and one that
  cannot. The reviews collected now become the set that later work is measured
  against, and a set where every row says the same thing measures nothing. A
  verdict with no name is lost data, so one is refused rather than defaulted.

When sign-in arrives this becomes the fallback for a session that has none, and
the reviews recorded in the meantime keep the names they were given.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request, Response

COOKIE = "watchdog_reviewer"

# A year. Long enough that nobody retypes it, short enough that a shared machine
# does not keep one person's name on it indefinitely.
COOKIE_MAX_AGE = 365 * 24 * 60 * 60

MAX_LENGTH = 80

# Control characters, which a name does not contain and a log line should not.
_FORBIDDEN = frozenset(chr(code) for code in range(0x20)) | {"\x7f"}


class InvalidName(ValueError):
    """A name Watchdog will not store. Shown as a plain sentence."""


def clean(value: str | None) -> str | None:
    """A usable name, or None. Raises when something was typed but is unusable."""
    if value is None:
        return None

    name = " ".join(value.split())
    if not name:
        return None

    if len(name) > MAX_LENGTH:
        raise InvalidName(f"A name can be at most {MAX_LENGTH} characters; that one is longer.")
    if any(character in _FORBIDDEN for character in name):
        raise InvalidName("A name cannot contain control characters.")

    return name


def get_reviewer(request: Request) -> str | None:
    """The name this browser is recording under, or None if it has not said."""
    try:
        return clean(request.cookies.get(COOKIE))
    except InvalidName:
        # A cookie somebody edited by hand. Treated as absent, so the page asks
        # again rather than failing on a request that did nothing wrong.
        return None


def remember(response: Response, name: str) -> None:
    """Keep the name in this browser. httponly because no script needs to read it."""
    response.set_cookie(
        COOKIE,
        name,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


def forget(response: Response) -> None:
    response.delete_cookie(COOKIE)


Reviewer = Annotated[str | None, Depends(get_reviewer)]
