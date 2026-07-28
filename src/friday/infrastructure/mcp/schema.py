"""Small, enforced and deliberately fail-closed MCP input-schema subset."""

from __future__ import annotations

import json
import math
from typing import cast

from friday.application.errors import ToolInputInvalid
from friday.domain.json_value import JsonValue
from friday.infrastructure.mcp.errors import McpProtocolError

MAX_SCHEMA_DEPTH = 8
MAX_SCHEMA_PROPERTIES = 64
MAX_SCHEMA_ENUM_VALUES = 64
_TYPES = frozenset({"object", "array", "string", "boolean", "integer", "number", "null"})
_KEYS = frozenset(
    {
        "type",
        "properties",
        "required",
        "items",
        "enum",
        "additionalProperties",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
    }
)
_ANNOTATIONS = frozenset(
    {
        "description",
        "title",
        "examples",
        "$comment",
        "default",
        "deprecated",
        "readOnly",
        "writeOnly",
        "format",
        "pattern",
    }
)


def normalize_input_schema(raw: object, *, max_bytes: int) -> dict[str, JsonValue]:
    if not isinstance(raw, dict):
        raise McpProtocolError("an MCP input schema must be a JSON object")
    if _json_bytes(raw) > max_bytes:
        raise McpProtocolError("an MCP input schema exceeded its configured bytes")
    schema = _walk(raw, 1)
    if schema.get("type") != "object":
        raise McpProtocolError("an MCP input schema root must be an object schema")
    return schema


def _walk(node: object, depth: int) -> dict[str, JsonValue]:
    if depth > MAX_SCHEMA_DEPTH or not isinstance(node, dict):
        raise McpProtocolError("an MCP input schema exceeded maximum depth")
    unknown = set(node) - _KEYS - _ANNOTATIONS
    if unknown:
        raise McpProtocolError("an MCP input schema used an unsupported keyword")
    declared = node.get("type")
    if not isinstance(declared, str) or declared not in _TYPES:
        raise McpProtocolError("an MCP input schema used an unsupported type")
    out: dict[str, JsonValue] = {"type": declared}
    if "properties" in node:
        properties = node["properties"]
        if not isinstance(properties, dict) or len(properties) > MAX_SCHEMA_PROPERTIES:
            raise McpProtocolError("schema properties are malformed or exceeded their bound")
        if any(not isinstance(name, str) or not name for name in properties):
            raise McpProtocolError("schema property names must be non-empty strings")
        out["properties"] = {
            name: _walk(value, depth + 1) for name, value in sorted(properties.items())
        }
    if "required" in node:
        required = node["required"]
        if not isinstance(required, list) or any(not isinstance(name, str) for name in required):
            raise McpProtocolError("schema required must be an array of strings")
        if len(set(required)) != len(required):
            raise McpProtocolError("schema required must not contain duplicates")
        properties = cast(dict[str, object], node.get("properties", {}))
        if any(name not in properties for name in required):
            raise McpProtocolError("schema required contains an unknown property")
        out["required"] = cast(JsonValue, list(required))
    if "items" in node:
        out["items"] = _walk(node["items"], depth + 1)
    if declared == "object" and "additionalProperties" not in node:
        # JSON Schema's default is "anything else is allowed". Friday's is not:
        # an unstated member is an unvalidated member reaching a remote service,
        # so an object schema that does not say otherwise closes.
        out["additionalProperties"] = False
    if "additionalProperties" in node:
        extra = node["additionalProperties"]
        if isinstance(extra, bool):
            out["additionalProperties"] = extra
        elif isinstance(extra, dict):
            out["additionalProperties"] = _walk(extra, depth + 1)
        else:
            raise McpProtocolError("schema additionalProperties must be a boolean or schema")
    if "enum" in node:
        values = node["enum"]
        if not isinstance(values, list) or not values or len(values) > MAX_SCHEMA_ENUM_VALUES:
            raise McpProtocolError("schema enum exceeded its bound")
        for value in values:
            _json_value(value, depth + 1)
        out["enum"] = cast(JsonValue, values)
    for key in ("minimum", "maximum"):
        if key in node:
            value = node[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise McpProtocolError(f"schema {key} must be numeric")
            out[key] = value
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in node:
            value = node[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise McpProtocolError(f"schema {key} must be a non-negative integer")
            out[key] = value
    return out


def validate_input(schema: JsonValue, value: JsonValue) -> None:
    if not isinstance(value, dict):
        raise ToolInputInvalid("tool input must be a JSON object")
    _check(schema, value, "input")


def _check(schema: JsonValue, value: JsonValue, path: str) -> None:
    if not isinstance(schema, dict) or not isinstance(schema.get("type"), str):
        raise ToolInputInvalid(f"{path}: invalid tool schema")
    declared = cast(str, schema["type"])
    if not _matches(declared, value):
        raise ToolInputInvalid(f"{path}: expected {declared}")
    if isinstance((enum := schema.get("enum")), list) and value not in enum:
        raise ToolInputInvalid(f"{path}: not an allowed value")
    if isinstance(value, dict):
        properties = cast(dict[str, JsonValue], schema.get("properties", {}))
        for name in cast(list[str], schema.get("required", [])):
            if name not in value:
                raise ToolInputInvalid(f"{path}: missing required property {name}")
        # JSON Schema defaults to allowing additional members.  An explicit
        # false must work even when properties is empty.
        extra = schema.get("additionalProperties", True)
        for name, item in value.items():
            if name in properties:
                _check(properties[name], item, f"{path}.{name}")
            elif extra is False:
                raise ToolInputInvalid(f"{path}: unknown property {name}")
            elif isinstance(extra, dict):
                _check(extra, item, f"{path}.{name}")
    elif isinstance(value, list):
        _bounded(value, schema, "minItems", "maxItems", path)
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                _check(schema["items"], item, f"{path}[{index}]")
    elif isinstance(value, str):
        _bounded(value, schema, "minLength", "maxLength", path)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) and value < minimum:
            raise ToolInputInvalid(f"{path}: violates minimum")
        if isinstance(maximum, (int, float)) and not isinstance(maximum, bool) and value > maximum:
            raise ToolInputInvalid(f"{path}: violates maximum")


def _bounded(
    value: object, schema: dict[str, JsonValue], minimum: str, maximum: str, path: str
) -> None:
    if not isinstance(value, (str, list)):
        raise ToolInputInvalid(f"{path}: invalid bounded value")
    size = len(value)
    if isinstance(schema.get(minimum), int) and size < cast(int, schema[minimum]):
        raise ToolInputInvalid(f"{path}: too short")
    if isinstance(schema.get(maximum), int) and size > cast(int, schema[maximum]):
        raise ToolInputInvalid(f"{path}: too long")


def _matches(kind: str, value: JsonValue) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "null": value is None,
    }[kind]


def _json_bytes(value: object) -> int:
    try:
        return len(
            json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise McpProtocolError("an MCP input schema must be JSON-safe") from exc


def _json_value(value: object, depth: int) -> None:
    if depth > MAX_SCHEMA_DEPTH:
        raise McpProtocolError("an MCP input schema exceeded maximum depth")
    if value is None or isinstance(value, (str, int, bool)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise McpProtocolError("an MCP input schema must be JSON-safe")
        return
    if isinstance(value, list):
        for child in value:
            _json_value(child, depth + 1)
    elif isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise McpProtocolError("an MCP input schema must be JSON-safe")
        for child in value.values():
            _json_value(child, depth + 1)
    else:
        raise McpProtocolError("an MCP input schema must be JSON-safe")
