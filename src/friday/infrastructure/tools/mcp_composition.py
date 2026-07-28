"""The narrow composition bridge into Friday's MCP substrate."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from friday.infrastructure.mcp.bindings import McpBindingRegistry, McpBoundTool, normalization_token
from friday.infrastructure.mcp.config import McpServerConfig
from friday.infrastructure.mcp.discovery import discover_server
from friday.infrastructure.mcp.errors import McpConfigInvalid
from friday.infrastructure.mcp.stdio_client import McpStdioClient
from friday.infrastructure.tools.mcp_gateway import McpServerStack, McpToolGateway

McpConfigurationInvalid = McpConfigInvalid
MAX_AGGREGATE_STARTUP_SECONDS = 120.0


@dataclass(frozen=True, slots=True)
class McpGatewayConfig:
    enabled: bool
    servers: tuple[McpServerConfig, ...]

    def __post_init__(self) -> None:
        if not self.enabled:
            return
        if not self.servers:
            raise McpConfigInvalid("MCP is enabled but no server is configured")
        ids = [server.server_id for server in self.servers]
        if len(set(ids)) != len(ids):
            raise McpConfigInvalid("duplicate MCP server_id")


def build_mcp_gateway(
    config: McpGatewayConfig,
    *,
    existing_tool_names: Iterable[str] = (),
    monotonic: Callable[[], float] = time.monotonic,
) -> McpToolGateway | None:
    if not config.enabled:
        return None
    _validate_configured_names(config.servers, existing_tool_names)
    clients: list[McpStdioClient] = []
    global_deadline = monotonic() + MAX_AGGREGATE_STARTUP_SECONDS
    try:
        stacks: list[McpServerStack] = []
        for server in config.servers:
            if not server.enabled:
                continue
            # Do not even construct a client once the aggregate budget is
            # exhausted: construction is intentionally side-effect free, but
            # discovery/spawn is not.
            if monotonic() >= global_deadline:
                break
            client = McpStdioClient(server)
            clients.append(client)
            stacks.append(
                McpServerStack(
                    server,
                    client,
                    discover_server(
                        client, server, startup_deadline=global_deadline, monotonic=monotonic
                    ),
                )
            )
        return McpToolGateway(stacks)
    except BaseException:
        for client in clients:
            with contextlib.suppress(Exception):
                client.close()
        raise


def _validate_configured_names(
    servers: tuple[McpServerConfig, ...], existing_tool_names: Iterable[str]
) -> None:
    """Reject unsafe config before it can spawn even one subprocess."""
    configured: list[McpBoundTool] = []
    existing = tuple(existing_tool_names)
    by_token: dict[str, str] = {}
    for name in existing:
        token = normalization_token(name)
        if token in by_token:
            raise McpConfigInvalid("Friday tool names normalize to the same token")
        by_token[token] = name
    for server in servers:
        if not server.enabled:
            continue
        for binding in server.bindings:
            if binding.local_name in existing:
                raise McpConfigInvalid("an MCP binding collides with an existing Friday tool")
            if normalization_token(binding.local_name) in by_token:
                raise McpConfigInvalid("an MCP binding normalizes to an existing Friday tool")
            configured.append(
                McpBoundTool(server.server_id, binding, {"type": "object"}, "configured")
            )
    McpBindingRegistry(configured)
