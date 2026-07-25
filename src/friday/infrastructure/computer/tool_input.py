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


def required_field(values: dict[str, JsonValue], name: str) -> JsonValue:
    if name not in values:
        raise ToolInputInvalid(f"missing required input field: {name}")
    return values[name]


def required_str(values: dict[str, JsonValue], name: str, *, max_chars: int) -> str:
    value = required_field(values, name)
    if not isinstance(value, str) or not value.strip():
        raise ToolInputInvalid(f"{name} must be a non-empty string")
    if len(value) > max_chars:
        raise ToolInputInvalid(f"{name} must not exceed {max_chars} characters")
    return value.strip()


def optional_bool(values: dict[str, JsonValue], name: str, *, default: bool) -> bool:
    value = values.get(name, default)
    if not isinstance(value, bool):
        raise ToolInputInvalid(f"{name} must be a boolean")
    return value


def optional_int(values: dict[str, JsonValue], name: str) -> int | None:
    """Read an optional integer with no bound of its own.

    Used for coordinate and element fields, whose real bounds are the captured
    window and the captured element set — not a number range. Returning None
    for "absent" is what lets the caller tell `element` apart from `x`/`y`
    instead of guessing from a zero.
    """
    if name not in values:
        return None
    value = values[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolInputInvalid(f"{name} must be an integer")
    return value


def bounded_signed_int(values: dict[str, JsonValue], name: str, *, maximum: int) -> int:
    """Read a signed integer whose magnitude may not exceed `maximum`, or 0."""
    value = values.get(name, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolInputInvalid(f"{name} must be an integer")
    if abs(value) > maximum:
        raise ToolInputInvalid(f"{name} magnitude must not exceed {maximum}")
    return value


def string_list(values: dict[str, JsonValue], name: str, *, max_items: int) -> tuple[str, ...]:
    """Read an optional bounded list of non-empty strings."""
    value = values.get(name, [])
    if not isinstance(value, list):
        raise ToolInputInvalid(f"{name} must be an array of strings")
    if len(value) > max_items:
        raise ToolInputInvalid(f"{name} must not contain more than {max_items} entries")
    entries: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            raise ToolInputInvalid(f"{name} must contain only non-empty strings")
        entries.append(entry.strip())
    return tuple(entries)
