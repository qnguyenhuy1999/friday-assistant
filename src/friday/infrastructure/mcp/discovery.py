from __future__ import annotations

from dataclasses import dataclass

from friday.infrastructure.mcp.bindings import McpBoundTool, compute_binding_fingerprint
from friday.infrastructure.mcp.client import McpClient, McpRemoteTool
from friday.infrastructure.mcp.config import McpServerConfig
from friday.infrastructure.mcp.errors import McpError, McpProtocolError
from friday.infrastructure.mcp.health import McpServerStatus
from friday.infrastructure.mcp.schema import normalize_input_schema


@dataclass(frozen=True, slots=True)
class McpServerDiscovery:
    server_id: str
    available: tuple[McpBoundTool, ...]
    unavailable_local_names: tuple[str, ...]
    ignored_remote_tool_count: int
    failure_code: str | None

    @property
    def status(self) -> McpServerStatus:
        return (
            "unavailable"
            if not self.available
            else "degraded"
            if self.unavailable_local_names or self.failure_code
            else "available"
        )


def discover_server(client: McpClient, server: McpServerConfig) -> McpServerDiscovery:
    names = tuple(sorted(binding.local_name for binding in server.bindings))
    if not server.enabled:
        return _empty(server.server_id, names, None)
    try:
        client.connect()
        remote = _index(client.list_tools())
    except McpError as exc:
        return _empty(server.server_id, names, exc.code)
    available: list[McpBoundTool] = []
    unavailable: list[str] = []
    for binding in server.bindings:
        if (tool := remote.get(binding.remote_tool_name)) is None:
            unavailable.append(binding.local_name)
            continue
        try:
            schema = normalize_input_schema(tool.input_schema, max_bytes=server.max_schema_bytes)
        except McpError:
            unavailable.append(binding.local_name)
            continue
        available.append(
            McpBoundTool(
                server.server_id,
                binding,
                schema,
                compute_binding_fingerprint(
                    server=server, binding=binding, normalized_schema=schema
                ),
            )
        )
    allowed = {binding.remote_tool_name for binding in server.bindings}
    return McpServerDiscovery(
        server.server_id,
        tuple(available),
        tuple(sorted(unavailable)),
        sum(name not in allowed for name in remote),
        None,
    )


def _index(tools: tuple[McpRemoteTool, ...]) -> dict[str, McpRemoteTool]:
    indexed: dict[str, McpRemoteTool] = {}
    for tool in tools:
        if tool.name in indexed:
            raise McpProtocolError("the MCP server advertised a duplicate tool name")
        indexed[tool.name] = tool
    return indexed


def _empty(server_id: str, names: tuple[str, ...], failure_code: str | None) -> McpServerDiscovery:
    return McpServerDiscovery(server_id, (), names, 0, failure_code)
