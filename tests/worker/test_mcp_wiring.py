from __future__ import annotations

from friday.infrastructure.tools.mcp_composition import McpGatewayConfig, build_mcp_gateway


def test_disabled_mcp_composition_performs_no_construction() -> None:
    assert build_mcp_gateway(McpGatewayConfig(enabled=False, servers=())) is None
