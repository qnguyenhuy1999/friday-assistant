from __future__ import annotations

from friday.infrastructure.mcp.client import McpCallResult
from friday.infrastructure.mcp.output import OutputBounds, normalize_call_result


def test_output_normalization_truncates_text_before_persistence() -> None:
    result = normalize_call_result(
        McpCallResult(text_blocks=("x" * 20,)), bounds=OutputBounds(1, 4, 100)
    )
    assert isinstance(result, dict)
    assert result["truncated"] is True
