"""Strict JSON-file settings for the opt-in MCP gateway."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from friday.domain.approval import ApprovalCategory
from friday.infrastructure.mcp.config import (
    DEFAULT_CALL_TIMEOUT_SECONDS,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_MAX_CONTENT_ITEMS,
    DEFAULT_MAX_DISCOVERED_TOOLS,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_MAX_SCHEMA_BYTES,
    DEFAULT_MAX_TEXT_CHARS,
    McpServerConfig,
    McpToolBinding,
)
from friday.infrastructure.tools.mcp_composition import McpConfigurationInvalid, McpGatewayConfig

MCP_ENV = ("FRIDAY_MCP_ENABLED", "FRIDAY_MCP_CONFIG_PATH")
MAX_CONFIG_BYTES = 1_000_000
_TOP = frozenset({"servers"})
_SERVER = frozenset(
    {
        "server_id",
        "enabled",
        "transport",
        "command",
        "env_from",
        "connect_timeout_seconds",
        "call_timeout_seconds",
        "max_discovered_tools",
        "max_schema_bytes",
        "max_output_bytes",
        "max_content_items",
        "max_text_chars",
        "bindings",
    }
)
_BINDING = frozenset(
    {
        "local_name",
        "remote_tool_name",
        "trusted_description",
        "read_only",
        "approval_required",
        "approval_category",
    }
)


@dataclass(frozen=True, slots=True)
class McpSettings:
    mcp_enabled: bool
    config_path: Path | None

    @classmethod
    def from_env(cls) -> McpSettings:
        raw = os.environ.get("FRIDAY_MCP_ENABLED", "false").strip().lower()
        if raw not in {"true", "false"}:
            raise ValueError("FRIDAY_MCP_ENABLED must be true or false")
        path = os.environ.get("FRIDAY_MCP_CONFIG_PATH")
        return cls(raw == "true", Path(path) if path else None)

    def gateway_config(self) -> McpGatewayConfig:
        if not self.mcp_enabled:
            return McpGatewayConfig(enabled=False, servers=())
        if self.config_path is None:
            raise McpConfigurationInvalid("FRIDAY_MCP_CONFIG_PATH is required when MCP is enabled")
        try:
            raw = self.config_path.read_bytes()
        except OSError as exc:
            raise McpConfigurationInvalid("MCP config file could not be read") from exc
        if len(raw) > MAX_CONFIG_BYTES:
            raise McpConfigurationInvalid("MCP config file exceeded configured bytes")
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise McpConfigurationInvalid("MCP config file must be valid JSON") from exc
        if not isinstance(document, dict):
            raise McpConfigurationInvalid("MCP config root must be an object")
        _reject_unknown(document, _TOP, "MCP config")
        entries = document.get("servers")
        if not isinstance(entries, list) or not entries:
            raise McpConfigurationInvalid("MCP config requires at least one server")
        return McpGatewayConfig(True, tuple(_server(entry) for entry in entries))


def _server(entry: Any) -> McpServerConfig:
    if not isinstance(entry, dict):
        raise McpConfigurationInvalid("MCP server must be an object")
    _reject_unknown(entry, _SERVER, "MCP server")
    bindings = entry.get("bindings")
    if not isinstance(bindings, list):
        raise McpConfigurationInvalid("MCP server bindings must be an array")
    command = _strings(entry.get("command"), "command", allow_empty=False)
    return McpServerConfig(
        server_id=_string(entry, "server_id"),
        enabled=_bool(entry, "enabled", True),
        command=command,
        env_from=_strings(entry.get("env_from", []), "env_from", allow_empty=True),
        transport=_string(entry, "transport", "stdio"),
        connect_timeout_seconds=_number(
            entry, "connect_timeout_seconds", DEFAULT_CONNECT_TIMEOUT_SECONDS
        ),
        call_timeout_seconds=_number(entry, "call_timeout_seconds", DEFAULT_CALL_TIMEOUT_SECONDS),
        max_discovered_tools=_integer(entry, "max_discovered_tools", DEFAULT_MAX_DISCOVERED_TOOLS),
        max_schema_bytes=_integer(entry, "max_schema_bytes", DEFAULT_MAX_SCHEMA_BYTES),
        max_output_bytes=_integer(entry, "max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES),
        max_content_items=_integer(entry, "max_content_items", DEFAULT_MAX_CONTENT_ITEMS),
        max_text_chars=_integer(entry, "max_text_chars", DEFAULT_MAX_TEXT_CHARS),
        bindings=tuple(_binding(binding) for binding in bindings),
    )


def _binding(entry: Any) -> McpToolBinding:
    if not isinstance(entry, dict):
        raise McpConfigurationInvalid("MCP binding must be an object")
    _reject_unknown(entry, _BINDING, "MCP binding")
    category = entry.get("approval_category")
    if not isinstance(category, str):
        raise McpConfigurationInvalid("approval_category must be a string")
    try:
        parsed = ApprovalCategory(category)
    except ValueError as exc:
        raise McpConfigurationInvalid("unknown approval_category") from exc
    return McpToolBinding(
        local_name=_string(entry, "local_name"),
        remote_tool_name=_string(entry, "remote_tool_name"),
        trusted_description=_string(entry, "trusted_description"),
        read_only=_bool(entry, "read_only", None),
        approval_required=_bool(entry, "approval_required", None),
        approval_category=parsed,
    )


def _reject_unknown(entry: dict[str, Any], allowed: frozenset[str], where: str) -> None:
    if unknown := set(entry) - allowed:
        raise McpConfigurationInvalid(f"{where}: unknown key(s) {sorted(unknown)}")


def _string(entry: dict[str, Any], name: str, default: str | None = None) -> str:
    value = entry.get(name, default)
    if not isinstance(value, str):
        raise McpConfigurationInvalid(f"{name} must be a string")
    return value


def _bool(entry: dict[str, Any], name: str, default: bool | None) -> bool:
    value = entry.get(name, default)
    if not isinstance(value, bool):
        raise McpConfigurationInvalid(f"{name} must be a boolean")
    return value


def _number(entry: dict[str, Any], name: str, default: float) -> float:
    value = entry.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise McpConfigurationInvalid(f"{name} must be a number")
    return float(value)


def _integer(entry: dict[str, Any], name: str, default: int) -> int:
    value = entry.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise McpConfigurationInvalid(f"{name} must be an integer")
    return value


def _strings(value: Any, name: str, *, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise McpConfigurationInvalid(f"{name} must be an array of strings")
    if not value and not allow_empty:
        raise McpConfigurationInvalid(f"{name} must not be empty")
    return tuple(value)
