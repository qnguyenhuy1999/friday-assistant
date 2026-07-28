"""Frozen MCP binding identities used for authorization and provenance."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from friday.domain.json_value import JsonValue
from friday.domain.tool_provenance import ToolProvenance
from friday.infrastructure.mcp.config import McpServerConfig, McpToolBinding
from friday.infrastructure.mcp.errors import McpConfigInvalid

BINDING_FINGERPRINT_VERSION = 2
PROVENANCE_KIND = "mcp"


def compute_transport_identity(
    server: McpServerConfig, execution_identity: JsonValue | None = None
) -> str:
    material: dict[str, JsonValue] = {
        "transport": server.transport,
        "argv": cast(list[JsonValue], list(server.command)),
        "env_names": cast(list[JsonValue], sorted(server.env_from)),
        "execution": execution_identity or {},
    }
    return _sha256_json(material)


def compute_binding_fingerprint(
    *,
    server: McpServerConfig,
    binding: McpToolBinding,
    normalized_schema: JsonValue,
    execution_identity: JsonValue | None = None,
) -> str:
    return _sha256_json(
        {
            "version": BINDING_FINGERPRINT_VERSION,
            "server_id": server.server_id,
            "local_name": binding.local_name,
            "remote_tool_name": binding.remote_tool_name,
            "transport_identity": compute_transport_identity(server, execution_identity),
            "schema_identity": _schema_identity(normalized_schema),
            "risk_policy": binding.risk_policy,
        }
    )


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
    return _sha256_json(schema)


def _sha256(material: str) -> str:
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _sha256_json(material: JsonValue) -> str:
    try:
        canonical = json.dumps(material, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise McpConfigInvalid("MCP binding identity must be JSON-safe") from exc
    return _sha256(canonical)
