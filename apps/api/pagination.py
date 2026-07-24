"""Opaque, delivery-owned pagination cursors and query-param types.

Every paginated list use case already returns its full, deterministically
ordered result set (see each repository's documented ordering) — there is
no cursor-aware SQL query yet, so a cursor here is simply an item offset,
opaquely encoded so callers never depend on its shape. This is a scoped
choice for this phase; a future move to cursor-aware SQL queries would key
cursors on the sort column instead of an offset.

`CursorQuery`/`LimitQuery` decode/validate as part of FastAPI's own request
validation (a `BeforeValidator` raising `ValueError`, and native `Query`
`ge`/`le` bounds) so a malformed cursor or out-of-range limit becomes a 422
through the `RequestValidationError` handler already registered in
`apps/api/errors.py` — no route ever needs its own try/except for this.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Annotated, TypeVar

from fastapi import Query
from pydantic import BeforeValidator

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

T = TypeVar("T")


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps(offset).encode()).decode()


def _decode_cursor(cursor: str | None) -> int | None:
    if cursor is None:
        return None
    try:
        offset = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"malformed pagination cursor: {cursor!r}") from exc
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError(f"malformed pagination cursor: {cursor!r}")
    return offset


CursorQuery = Annotated[
    int | None,
    BeforeValidator(_decode_cursor),
    Query(alias="cursor", description="Opaque cursor from a previous page."),
]
LimitQuery = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: list[T]
    next_cursor: str | None


def paginate[T](items: list[T], *, cursor: int | None, limit: int) -> Page[T]:
    offset = cursor or 0
    page_items = items[offset : offset + limit]
    next_offset = offset + len(page_items)
    next_cursor = encode_cursor(next_offset) if next_offset < len(items) else None
    return Page(items=page_items, next_cursor=next_cursor)
