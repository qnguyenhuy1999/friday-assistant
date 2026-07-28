"""The narrow composition bridge into Friday's MCP substrate."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

from friday.infrastructure.mcp.bindings import McpBindingRegistry, McpBoundTool
from friday.infrastructure.mcp.config import McpServerConfig
from friday.infrastructure.mcp.discovery import discover_server
from friday.infrastructure.mcp.errors import McpConfigInvalid
from friday.infrastructure.mcp.stdio_client import McpStdioClient
from friday.infrastructure.tools.mcp_gateway import McpServerStack, McpToolGateway

McpConfigurationInvalid = McpConfigInvalid


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


def build_mcp_gateway(config: McpGatewayConfig) -> McpToolGateway | None:
    if not config.enabled:
        return None
    _validate_configured_names(config.servers)
    clients: list[McpStdioClient] = []
    try:
        stacks: list[McpServerStack] = []
        for server in config.servers:
            if not server.enabled:
                continue
            client = McpStdioClient(server)
            clients.append(client)
            stacks.append(McpServerStack(server, client, discover_server(client, server)))
        return McpToolGateway(stacks)
    except BaseException:
        for client in clients:
            with contextlib.suppress(Exception):
                client.close()
        raise


def _validate_configured_names(servers: tuple[McpServerConfig, ...]) -> None:
    """Reject unsafe config before it can spawn even one subprocess."""
    configured: list[McpBoundTool] = []
    for server in servers:
        if not server.enabled:
            continue
        for binding in server.bindings:
            if binding.local_name.startswith(("workspace.", "computer.")):
                raise McpConfigInvalid(
                    "an MCP binding may not use the reserved workspace/computer namespace"
                )
            configured.append(
                McpBoundTool(server.server_id, binding, {"type": "object"}, "configured")
            )
    McpBindingRegistry(configured)
