from __future__ import annotations

from pathlib import Path

from friday.domain.approval import ApprovalCategory
from friday.infrastructure.mcp.config import McpServerConfig, McpToolBinding
from friday.infrastructure.mcp.stdio_client import McpStdioClient
from tests.infrastructure.mcp_fixture_server import make_fixture_server


def test_stdio_client_handshakes_lists_and_calls(tmp_path: Path) -> None:
    config = McpServerConfig(
        server_id="fixture",
        enabled=True,
        command=make_fixture_server(tmp_path),
        bindings=(
            McpToolBinding(
                local_name="fixture.read",
                remote_tool_name="read",
                trusted_description="Fixture read.",
                read_only=True,
                approval_required=False,
                approval_category=ApprovalCategory.NETWORK_ACCESS,
            ),
        ),
    )
    client = McpStdioClient(config)
    try:
        assert client.connect() == "2025-06-18"
        assert tuple(tool.name for tool in client.list_tools()) == ("read", "write")
        assert client.call_tool("read", {"key": "a"}, timeout_seconds=1).structured_content == {
            "key": "a",
            "value": None,
        }
    finally:
        client.close()
