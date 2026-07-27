"""Sanitize untrusted schemas and locally reject obviously invalid calls."""

from __future__ import annotations

import json
from typing import cast

from friday.application.errors import ToolInputInvalid
from friday.domain.errors import DomainValidationError
from friday.domain.json_value import JsonValue, ensure_json_value
from friday.infrastructure.mcp.errors import McpProtocolError

MAX_SCHEMA_DEPTH = 8
MAX_SCHEMA_PROPERTIES = 64
MAX_SCHEMA_ENUM_VALUES = 64
STRUCTURAL_KEYS = frozenset(
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
        "pattern",
        "format",
    }
)
ANNOTATION_KEYS = frozenset(
    {
        "description",
        "title",
        "examples",
        "$comment",
        "default",
        "deprecated",
        "readOnly",
        "writeOnly",
    }
)
FORBIDDEN_KEYS = frozenset(
    {"$ref", "$defs", "definitions", "$dynamicRef", "$dynamicAnchor", "$anchor"}
)


def normalize_input_schema(raw: JsonValue, *, max_bytes: int) -> dict[str, JsonValue]:
    if raw is None:
        return {"type": "object"}
    if not isinstance(raw, dict):
        raise McpProtocolError("an MCP input schema must be a JSON object")
    try:
        ensure_json_value(raw, path="$.inputSchema")
    except DomainValidationError as exc:
        raise McpProtocolError("an MCP input schema must be JSON-safe") from exc
    if len(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()) > max_bytes:
        raise McpProtocolError("an MCP input schema exceeded its configured bytes")
    return _walk(raw, 1)


def _walk(node: JsonValue, depth: int) -> dict[str, JsonValue]:
    if depth > MAX_SCHEMA_DEPTH:
        raise McpProtocolError("an MCP input schema exceeded maximum depth")
    if not isinstance(node, dict):
        raise McpProtocolError("an MCP input schema member must be an object")
    if FORBIDDEN_KEYS.intersection(node):
        raise McpProtocolError("an MCP input schema used a reference keyword")
    out: dict[str, JsonValue] = {}
    for key, value in node.items():
        if key not in STRUCTURAL_KEYS:
            continue
        if key == "properties":
            if not isinstance(value, dict):
                raise McpProtocolError("properties must be an object")
            if len(value) > MAX_SCHEMA_PROPERTIES:
                raise McpProtocolError("schema has too many properties")
            out[key] = {name: _walk(schema, depth + 1) for name, schema in sorted(value.items())}
        elif key == "items":
            out[key] = _walk(value, depth + 1)
        elif key == "enum":
            if not isinstance(value, list) or len(value) > MAX_SCHEMA_ENUM_VALUES:
                raise McpProtocolError("schema enum exceeds bound")
            out[key] = value
        else:
            out[key] = value
    return out


def validate_input(schema: JsonValue, value: JsonValue) -> None:
    if not isinstance(value, dict):
        raise ToolInputInvalid("tool input must be a JSON object")
    _check(schema, value, "input")


def _check(schema: JsonValue, value: JsonValue, path: str) -> None:
    if not isinstance(schema, dict):
        return
    declared = schema.get("type")
    if isinstance(declared, str) and not _matches(declared, value):
        raise ToolInputInvalid(f"{path}: expected {declared}")
    if isinstance((enum := schema.get("enum")), list) and value not in enum:
        raise ToolInputInvalid(f"{path}: not an allowed value")
    if isinstance(value, dict):
        properties = (
            cast(dict[str, JsonValue], schema["properties"])
            if isinstance(schema.get("properties"), dict)
            else {}
        )
        required = schema.get("required")
        if isinstance(required, list):
            for name in required:
                if isinstance(name, str) and name not in value:
                    raise ToolInputInvalid(f"{path}: missing required property {name}")
        for name, item in value.items():
            if name in properties:
                _check(properties[name], item, f"{path}.{name}")
            elif properties and schema.get("additionalProperties") is not True:
                raise ToolInputInvalid(f"{path}: unknown property {name}")
    elif isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _check(schema["items"], item, f"{path}[{index}]")
    elif isinstance(value, str):
        if isinstance((maximum := schema.get("maxLength")), int) and len(value) > maximum:
            raise ToolInputInvalid(f"{path}: too long")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            raise ToolInputInvalid(f"{path}: violates minimum")
        if isinstance(maximum, (int, float)) and value > maximum:
            raise ToolInputInvalid(f"{path}: violates maximum")


def _matches(kind: str, value: JsonValue) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "null": value is None,
    }.get(kind, True)
