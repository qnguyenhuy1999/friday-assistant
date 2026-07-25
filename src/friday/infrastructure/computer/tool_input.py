"""Strict input parsing for computer-use tools.

Stricter than the workspace tools on purpose: unknown fields are rejected
rather than ignored. Elsewhere a stray key is harmless noise, but here it
means Claude and Friday disagree about what was requested — and the
disagreement would be resolved by clicking somewhere. A refused call is
always cheaper than a side effect nobody asked for.

Every helper raises ToolInputInvalid, which the gateway maps onto the
`tool_invalid_input` failure code.
"""

from __future__ import annotations

from friday.application.errors import ToolInputInvalid
from friday.domain.json_value import JsonValue

NO_FIELDS: frozenset[str] = frozenset()


def parse_object(value: JsonValue, *, allowed: frozenset[str]) -> dict[str, JsonValue]:
    """Return `value` as a mapping, rejecting anything it does not declare."""
    if not isinstance(value, dict):
        raise ToolInputInvalid("tool input must be an object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ToolInputInvalid(f"unknown input field(s): {unknown}")
    return value


def optional_bounded_int(
    values: dict[str, JsonValue], name: str, *, maximum: int, default: int | None = None
) -> int:
    """Read a positive integer no larger than `maximum`, defaulting to it.

    Booleans are rejected explicitly: `True` is an `int` in Python, and a
    limit of 1 arrived at by accident is a silent truncation.
    """
    fallback = maximum if default is None else default
    value = values.get(name, fallback)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolInputInvalid(f"{name} must be an integer")
    if value < 1 or value > maximum:
        raise ToolInputInvalid(f"{name} must be an integer between 1 and {maximum}")
    return value
