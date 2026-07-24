"""Opaque cursor pagination owned by the HTTP delivery layer.

The current repository ports return ordered lists rather than accepting
cursors, so these helpers deliberately slice those lists in the API layer.
That keeps this SQLite-scale delivery change local until persistence needs
true keyset queries.

Cursors encode the last-seen item's sort-key tuple (not a raw offset), so
a page stays stable even if items are inserted or removed between reads --
an offset-based cursor would skip or repeat items under concurrent writes.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable, Sequence

from fastapi import HTTPException

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


def encode_cursor(*parts: str | int) -> str:
    payload = json.dumps(["v1", *parts], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_cursor(cursor: str, expected_parts: int) -> tuple[str, ...]:
    try:
        decoded = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(decoded)
        if (
            not isinstance(payload, list)
            or len(payload) != expected_parts + 1
            or payload[0] != "v1"
            or any(not isinstance(value, (str, int)) for value in payload[1:])
        ):
            raise ValueError
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
        raise HTTPException(status_code=422, detail="Invalid pagination cursor") from None
    return tuple(str(value) for value in payload[1:])


def page_ordered[T](
    items: Sequence[T],
    *,
    limit: int,
    cursor: str | None,
    key: Callable[[T], tuple[str, ...]],
) -> tuple[list[T], str | None]:
    start = 0
    if cursor is not None:
        cursor_key = decode_cursor(cursor, len(key(items[0])) if items else 2)
        for index, item in enumerate(items):
            if key(item) == cursor_key:
                start = index + 1
                break
        else:
            raise HTTPException(status_code=422, detail="Invalid pagination cursor")
    page = list(items[start : start + limit])
    next_cursor = encode_cursor(*key(page[-1])) if start + limit < len(items) and page else None
    return page, next_cursor
