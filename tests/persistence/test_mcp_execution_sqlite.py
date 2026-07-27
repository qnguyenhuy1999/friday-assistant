from __future__ import annotations

from friday.domain.tool_provenance import ToolProvenance


def test_mcp_provenance_is_a_durable_vendor_free_value_object() -> None:
    provenance = ToolProvenance("mcp", "fixture", "read", "a" * 64)
    assert provenance.target == "fixture"
