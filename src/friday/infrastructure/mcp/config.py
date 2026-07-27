"""Immutable, validated, secret-free operator MCP configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from math import isfinite

from friday.application.runtime_actions import TOOL_NAME_PATTERN
from friday.domain.approval import ApprovalCategory
from friday.infrastructure.mcp.errors import McpConfigInvalid
from friday.infrastructure.mcp.process_policy import validate_server_command

STDIO_TRANSPORT = "stdio"
SUPPORTED_TRANSPORTS = frozenset({STDIO_TRANSPORT})
SERVER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
MAX_REMOTE_TOOL_NAME_CHARS = 128
MAX_TRUSTED_DESCRIPTION_CHARS = 500
MAX_TIMEOUT_SECONDS = 300.0
MAX_DISCOVERED_TOOLS_CEILING = 500
MAX_SCHEMA_BYTES_CEILING = 262_144
MAX_OUTPUT_BYTES_CEILING = 1_048_576
MAX_CONTENT_ITEMS_CEILING = 256
MAX_TEXT_CHARS_CEILING = 200_000
DEFAULT_CONNECT_TIMEOUT_SECONDS = 15.0
DEFAULT_CALL_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_DISCOVERED_TOOLS = 200
DEFAULT_MAX_SCHEMA_BYTES = 32_768
DEFAULT_MAX_OUTPUT_BYTES = 65_536
DEFAULT_MAX_CONTENT_ITEMS = 16
DEFAULT_MAX_TEXT_CHARS = 8_000


@dataclass(frozen=True, slots=True)
class McpToolBinding:
    local_name: str
    remote_tool_name: str
    trusted_description: str
    read_only: bool
    approval_required: bool
    approval_category: ApprovalCategory

    def __post_init__(self) -> None:
        if not TOOL_NAME_PATTERN.match(self.local_name):
            raise McpConfigInvalid("binding local_name does not match the Friday tool grammar")
        if (
            not self.remote_tool_name.strip()
            or len(self.remote_tool_name) > MAX_REMOTE_TOOL_NAME_CHARS
        ):
            raise McpConfigInvalid("remote_tool_name must be non-empty and bounded")
        if (
            not self.trusted_description.strip()
            or len(self.trusted_description) > MAX_TRUSTED_DESCRIPTION_CHARS
        ):
            raise McpConfigInvalid("trusted_description must be non-empty and bounded")
        if not self.read_only and not self.approval_required:
            raise McpConfigInvalid("a mutating MCP binding must require approval")

    @property
    def risk_policy(self) -> str:
        mode = "ro" if self.read_only else "rw"
        approval = "approval" if self.approval_required else "noapproval"
        return f"{mode}:{approval}:{self.approval_category.value}"


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    server_id: str
    enabled: bool
    command: tuple[str, ...]
    bindings: tuple[McpToolBinding, ...]
    env_from: tuple[str, ...] = ()
    transport: str = STDIO_TRANSPORT
    connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    call_timeout_seconds: float = DEFAULT_CALL_TIMEOUT_SECONDS
    max_discovered_tools: int = DEFAULT_MAX_DISCOVERED_TOOLS
    max_schema_bytes: int = DEFAULT_MAX_SCHEMA_BYTES
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    max_content_items: int = DEFAULT_MAX_CONTENT_ITEMS
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS
    max_discovery_bytes: int = field(default=0)

    def __post_init__(self) -> None:
        if not SERVER_ID_PATTERN.match(self.server_id):
            raise McpConfigInvalid("server_id must be a lowercase token")
        if self.transport not in SUPPORTED_TRANSPORTS:
            raise McpConfigInvalid("transport is not supported")
        validate_server_command(self.command)
        self._validate_env_from()
        self._validate_bounds()
        self._validate_bindings()

    def _validate_env_from(self) -> None:
        if len(set(self.env_from)) != len(self.env_from):
            raise McpConfigInvalid("env_from must not contain duplicate names")
        for name in self.env_from:
            if "=" in name or not ENV_NAME_PATTERN.match(name):
                raise McpConfigInvalid("env_from must contain variable names only")

    def _validate_bounds(self) -> None:
        for name, value in (
            ("connect_timeout_seconds", self.connect_timeout_seconds),
            ("call_timeout_seconds", self.call_timeout_seconds),
        ):
            if not isfinite(value) or value <= 0 or value > MAX_TIMEOUT_SECONDS:
                raise McpConfigInvalid(f"{name} must be positive, finite, and bounded")
        for name, value, ceiling in (
            ("max_discovered_tools", self.max_discovered_tools, MAX_DISCOVERED_TOOLS_CEILING),
            ("max_schema_bytes", self.max_schema_bytes, MAX_SCHEMA_BYTES_CEILING),
            ("max_output_bytes", self.max_output_bytes, MAX_OUTPUT_BYTES_CEILING),
            ("max_content_items", self.max_content_items, MAX_CONTENT_ITEMS_CEILING),
            ("max_text_chars", self.max_text_chars, MAX_TEXT_CHARS_CEILING),
        ):
            if value < 1 or value > ceiling:
                raise McpConfigInvalid(f"{name} must be positive and bounded")

    def _validate_bindings(self) -> None:
        if not self.bindings:
            raise McpConfigInvalid("at least one binding is required")
        local_names = [binding.local_name for binding in self.bindings]
        remote_names = [binding.remote_tool_name for binding in self.bindings]
        if len(set(local_names)) != len(local_names):
            raise McpConfigInvalid("duplicate binding local_name")
        if len(set(remote_names)) != len(remote_names):
            raise McpConfigInvalid("duplicate binding remote_tool_name")

    @property
    def discovery_byte_budget(self) -> int:
        return (
            self.max_discovery_bytes
            or self.max_schema_bytes * self.max_discovered_tools + 64 * 1024
        )
