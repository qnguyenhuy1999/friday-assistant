from __future__ import annotations

from friday.domain.approval import ApprovalCategory
from friday.infrastructure.mcp.client import McpRemoteTool
from friday.infrastructure.mcp.config import McpServerConfig, McpToolBinding
from friday.infrastructure.mcp.discovery import discover_server


class _Client:
    def connect(self) -> str:
        return "2025-06-18"

    def list_tools(self) -> tuple[McpRemoteTool, ...]:
        return (McpRemoteTool("read", {"type": "object"}),)

    def call_tool(self, *args: object, **kwargs: object) -> object:
        raise AssertionError

    def close(self) -> None:
        pass


def test_discovery_intersects_remote_tools_with_operator_bindings() -> None:
    binding = McpToolBinding(
        "fixture.read", "read", "Read.", True, False, ApprovalCategory.NETWORK_ACCESS
    )
    config = McpServerConfig("fixture", True, ("fixture",), (binding,))
    assert discover_server(_Client(), config).available[0].local_name == "fixture.read"  # type: ignore[arg-type]
