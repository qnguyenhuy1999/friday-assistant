from __future__ import annotations

from friday.domain.approval import ApprovalCategory
from friday.infrastructure.mcp.bindings import (
    McpBindingRegistry,
    McpBoundTool,
    compute_binding_fingerprint,
)
from friday.infrastructure.mcp.config import McpServerConfig, McpToolBinding


def test_binding_registry_exposes_fingerprinted_external_scope() -> None:
    binding = McpToolBinding(
        "fixture.read", "read", "Read.", True, False, ApprovalCategory.NETWORK_ACCESS
    )
    server = McpServerConfig("fixture", True, ("fixture-server",), (binding,))
    tool = McpBoundTool(
        "fixture",
        binding,
        {"type": "object"},
        compute_binding_fingerprint(
            server=server, binding=binding, normalized_schema={"type": "object"}
        ),
    )
    assert McpBindingRegistry((tool,)).get("fixture.read").authorization_scope.startswith("mcp:")  # type: ignore[union-attr]
