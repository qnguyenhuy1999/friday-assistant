"""Frozen MCP binding identities used for authorization and provenance."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass

from friday.domain.json_value import JsonValue
from friday.domain.tool_provenance import ToolProvenance
from friday.infrastructure.mcp.config import McpServerConfig, McpToolBinding
from friday.infrastructure.mcp.errors import McpConfigInvalid

BINDING_FINGERPRINT_VERSION = 1
PROVENANCE_KIND = "mcp"
_UNIT_SEPARATOR = "\x1f"


def compute_transport_identity(server: McpServerConfig) -> str:
    material = _UNIT_SEPARATOR.join(
        [server.transport, *server.command, _UNIT_SEPARATOR.join(sorted(server.env_from))]
    )
    return _sha256(material)


def compute_binding_fingerprint(
    *, server: McpServerConfig, binding: McpToolBinding, normalized_schema: JsonValue
) -> str:
    material = "\n".join(
        [
            str(BINDING_FINGERPRINT_VERSION),
            server.server_id,
            binding.local_name,
            binding.remote_tool_name,
            compute_transport_identity(server),
            _schema_identity(normalized_schema),
            binding.risk_policy,
        ]
    )
    return _sha256(material)


@dataclass(frozen=True, slots=True)
class McpBoundTool:
    server_id: str
    binding: McpToolBinding
    normalized_schema: JsonValue
    binding_fingerprint: str

    @property
    def local_name(self) -> str:
        return self.binding.local_name

    @property
    def remote_tool_name(self) -> str:
        return self.binding.remote_tool_name

    @property
    def authorization_scope(self) -> str:
        return f"{PROVENANCE_KIND}:{self.binding_fingerprint}"

    @property
    def provenance(self) -> ToolProvenance:
        return ToolProvenance(
            kind=PROVENANCE_KIND,
            target=self.server_id,
            remote_name=self.remote_tool_name,
            binding_fingerprint=self.binding_fingerprint,
        )

    @property
    def approval_summary(self) -> str:
        mode = "read-only" if self.binding.read_only else "mutating"
        return f"{self.server_id}: {self.remote_tool_name} — {mode} external service operation"


class McpBindingRegistry:
    def __init__(self, bound: Sequence[McpBoundTool]) -> None:
        by_name: dict[str, McpBoundTool] = {}
        by_token: dict[str, str] = {}
        for tool in bound:
            name = tool.local_name
            if name in by_name:
                raise McpConfigInvalid(f"MCP tool name registered by more than one binding: {name}")
            token = normalization_token(name)
            if (collided := by_token.get(token)) is not None:
                raise McpConfigInvalid(
                    f"MCP tool names {collided!r} and {name!r} normalize to the same token"
                )
            by_name[name] = tool
            by_token[token] = name
        self._by_name = by_name

    def local_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_name))

    def get(self, local_name: str) -> McpBoundTool | None:
        return self._by_name.get(local_name)

    def __len__(self) -> int:
        return len(self._by_name)


def normalization_token(local_name: str) -> str:
    return local_name.replace(".", "_").replace("-", "_")


def _schema_identity(schema: JsonValue) -> str:
    return _sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")))


def _sha256(material: str) -> str:
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
