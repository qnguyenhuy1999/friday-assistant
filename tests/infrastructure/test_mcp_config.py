from __future__ import annotations

import pytest

from friday.domain.approval import ApprovalCategory
from friday.infrastructure.mcp.config import McpServerConfig, McpToolBinding
from friday.infrastructure.mcp.errors import McpConfigInvalid


def test_mcp_configuration_requires_explicit_safe_binding_policy() -> None:
    with pytest.raises(McpConfigInvalid):
        McpToolBinding(
            "fixture.write", "write", "Write.", False, False, ApprovalCategory.NETWORK_ACCESS
        )
    config = McpServerConfig(
        "fixture",
        True,
        ("fixture-server",),
        (
            McpToolBinding(
                "fixture.read", "read", "Read.", True, False, ApprovalCategory.NETWORK_ACCESS
            ),
        ),
    )
    assert config.discovery_byte_budget > config.max_schema_bytes
