from __future__ import annotations

from friday.infrastructure.tools.mcp_gateway import McpToolGateway


def test_empty_mcp_gateway_has_no_manifest_or_health_entries() -> None:
    gateway = McpToolGateway(())
    assert gateway.list_tools() == ()
    assert gateway.health() == ()
