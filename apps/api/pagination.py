"""Opaque, collection-bound keyset cursors for HTTP collection endpoints."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


@dataclass(frozen=True)
class Cursor:
    after: tuple[str, ...]


def encode_cursor(
    *, collection: str, parent_id: str | None, order: str, after: Sequence[str | int]
) -> str:
    payload = {
        "version": 1,
        "collection": collection,
        "parent_id": parent_id,
        "order": order,
        "after": list(after),
    }
    return (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def decode_cursor(
    value: str | None, *, collection: str, parent_id: str | None, order: str, parts: int
) -> Cursor | None:
    if value is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        payload = json.loads(raw)
        after = payload["after"]
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or payload.get("collection") != collection
            or payload.get("parent_id") != parent_id
            or payload.get("order") != order
            or not isinstance(after, list)
            or len(after) != parts
            or any(not isinstance(part, (str, int)) for part in after)
        ):
            raise ValueError
    except (
        KeyError,
        ValueError,
        TypeError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ):
        raise HTTPException(status_code=422, detail="Invalid pagination cursor") from None
    return Cursor(tuple(str(part) for part in after))


def page_from_query[T](
    rows: Sequence[T],
    *,
    limit: int,
    collection: str,
    parent_id: str | None,
    order: str,
    key: Callable[[T], tuple[str | int, ...]],
) -> tuple[list[T], str | None]:
    """Turn the bounded ``LIMIT page_size + 1`` result into an HTTP page."""
    page = list(rows[:limit])
    next_cursor = None
    if len(rows) > limit and page:
        next_cursor = encode_cursor(
            collection=collection, parent_id=parent_id, order=order, after=key(page[-1])
        )
    return page, next_cursor


def cursor_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid pagination cursor") from None


def cursor_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid pagination cursor") from None
