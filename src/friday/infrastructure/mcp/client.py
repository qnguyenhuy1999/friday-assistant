"""Transport seam that never carries remote descriptions or binary payloads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from friday.domain.json_value import JsonValue

SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26")
CLIENT_NAME = "friday-mcp"
CLIENT_VERSION = "1"


@dataclass(frozen=True, slots=True)
class McpRemoteTool:
    name: str
    input_schema: object


@dataclass(frozen=True, slots=True)
class McpCallResult:
    structured_content: JsonValue | None = None
    text_blocks: tuple[str, ...] = ()
    omitted_block_kinds: tuple[str, ...] = ()
    total_content_items: int = 0


class McpClient(Protocol):
    def connect(self) -> str: ...
    def list_tools(self) -> tuple[McpRemoteTool, ...]: ...
    def call_tool(
        self,
        name: str,
        arguments: dict[str, JsonValue],
        *,
        timeout_seconds: float,
        cancelled: Callable[[], bool] | None = None,
    ) -> McpCallResult: ...
    def close(self) -> None: ...
