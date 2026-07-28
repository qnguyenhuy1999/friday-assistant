from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from friday.domain.json_value import JsonValue
from friday.infrastructure.mcp.client import McpCallResult
from friday.infrastructure.mcp.config import McpServerConfig
from friday.infrastructure.mcp.errors import McpInvalidOutput

MAX_OUTPUT_DEPTH = 12


@dataclass(frozen=True, slots=True)
class OutputBounds:
    max_content_items: int
    max_text_chars: int
    max_result_bytes: int
    max_depth: int = MAX_OUTPUT_DEPTH

    @classmethod
    def from_server(cls, server: McpServerConfig) -> OutputBounds:
        return cls(server.max_content_items, server.max_text_chars, server.max_output_bytes)


def normalize_call_result(result: McpCallResult, *, bounds: OutputBounds) -> JsonValue:
    if result.total_content_items > bounds.max_content_items:
        raise McpInvalidOutput("the MCP result exceeded its content-item bound")
    if result.structured_content is not None:
        try:
            _json_bytes(result.structured_content)
            _depth(result.structured_content, bounds.max_depth, 1)
        except (TypeError, ValueError, RecursionError) as exc:
            raise McpInvalidOutput("the MCP result was not JSON-safe") from exc
    text = [
        block[: bounds.max_text_chars] for block in result.text_blocks[: bounds.max_content_items]
    ]
    envelope: dict[str, JsonValue] = {
        "structured": result.structured_content,
        "text": cast(list[JsonValue], text),
        "omitted_block_kinds": cast(list[JsonValue], list(result.omitted_block_kinds)),
        "truncated": len(text) < len(result.text_blocks)
        or result.total_content_items > len(result.text_blocks)
        or any(
            len(block) > bounds.max_text_chars
            for block in result.text_blocks[: bounds.max_content_items]
        ),
    }
    if _json_bytes(envelope) > bounds.max_result_bytes:
        raise McpInvalidOutput("the MCP result exceeded its configured bytes")
    return envelope


def _depth(value: JsonValue, limit: int, current: int) -> None:
    if current > limit:
        raise McpInvalidOutput("the MCP result exceeded maximum depth")
    if isinstance(value, dict):
        for member in value.values():
            _depth(member, limit, current + 1)
    elif isinstance(value, list):
        for member in value:
            _depth(member, limit, current + 1)


def _json_bytes(value: JsonValue) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
