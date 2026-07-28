from __future__ import annotations

from collections.abc import Callable

import pytest

from friday.domain.approval import ApprovalCategory
from friday.domain.json_value import JsonValue
from friday.infrastructure.mcp.client import McpCallResult, McpRemoteTool
from friday.infrastructure.mcp.config import McpServerConfig, McpToolBinding
from friday.infrastructure.mcp.discovery import discover_server


class _Client:
    def connect(self, *, timeout_seconds: float | None = None) -> str:
        del timeout_seconds
        return "2025-06-18"

    def list_tools(self, *, timeout_seconds: float | None = None) -> tuple[McpRemoteTool, ...]:
        del timeout_seconds
        return (McpRemoteTool("read", {"type": "object"}),)

    def call_tool(
        self,
        name: str,
        arguments: dict[str, JsonValue],
        *,
        timeout_seconds: float,
        cancelled: Callable[[], bool] | None = None,
    ) -> McpCallResult:
        del name, arguments, timeout_seconds, cancelled
        raise AssertionError

    def close(self) -> None:
        pass


def test_discovery_intersects_remote_tools_with_operator_bindings() -> None:
    binding = McpToolBinding(
        "fixture.read", "read", "Read.", True, False, ApprovalCategory.NETWORK_ACCESS
    )
    config = McpServerConfig("fixture", True, ("fixture",), (binding,))
    assert discover_server(_Client(), config).available[0].local_name == "fixture.read"


@pytest.mark.parametrize("operation", ["connect", "list_tools"])
def test_internal_typeerror_is_not_retried_without_the_startup_budget(operation: str) -> None:
    class Broken(_Client):
        def __init__(self) -> None:
            self.connect_calls = 0
            self.list_calls = 0

        def connect(self, *, timeout_seconds: float | None = None) -> str:
            del timeout_seconds
            self.connect_calls += 1
            if operation == "connect":
                raise TypeError("implementation bug")
            return "2025-06-18"

        def list_tools(self, *, timeout_seconds: float | None = None) -> tuple[McpRemoteTool, ...]:
            del timeout_seconds
            self.list_calls += 1
            if operation == "list_tools":
                raise TypeError("implementation bug")
            return super().list_tools()

    binding = McpToolBinding(
        "fixture.read", "read", "Read.", True, False, ApprovalCategory.NETWORK_ACCESS
    )
    client = Broken()
    with pytest.raises(TypeError, match="implementation bug"):
        discover_server(
            client,
            McpServerConfig("fixture", True, ("fixture",), (binding,)),
            startup_deadline=10,
            monotonic=lambda: 0,
        )
    assert client.connect_calls == 1
    assert client.list_calls == (0 if operation == "connect" else 1)
