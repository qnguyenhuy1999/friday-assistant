"""JSON-compatible value validation, shared by every entity that stores
arbitrary structured payloads (tool input/output, event payloads, artifact
metadata).

Deliberately centralized here rather than in a generic ``utils`` module:
this is the one domain-owned concern of "is this value safe to cross a
JSON wire contract," used by task.py, run.py, event.py, artifact.py, and
tool.py alike.
"""

from __future__ import annotations

import math

from friday.domain.errors import DomainValidationError

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def ensure_json_value(value: object, *, path: str = "$") -> JsonValue:
    """Validate that `value` is JSON-compatible, returning it unchanged.

    Rejects datetimes, UUIDs, sets, tuples, non-string mapping keys, and
    non-finite floats (NaN/Infinity) — all valid Python values but invalid
    JSON, and easy to construct by accident from domain code that forgot to
    serialize a timestamp or ID first.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DomainValidationError(f"{path}: non-finite float is not JSON-compatible")
        return value
    if isinstance(value, list):
        return [ensure_json_value(item, path=f"{path}[{i}]") for i, item in enumerate(value)]
    if isinstance(value, dict):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise DomainValidationError(f"{path}: mapping key {key!r} is not a string")
            result[key] = ensure_json_value(item, path=f"{path}.{key}")
        return result
    raise DomainValidationError(f"{path}: {type(value).__name__} is not JSON-compatible")
