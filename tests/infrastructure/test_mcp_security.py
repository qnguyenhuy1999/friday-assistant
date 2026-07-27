from __future__ import annotations

import pytest

from friday.infrastructure.mcp.config import McpServerConfig
from friday.infrastructure.mcp.errors import McpConfigInvalid


def test_shell_interpreter_cannot_be_an_mcp_server_command() -> None:
    with pytest.raises(McpConfigInvalid):
        McpServerConfig("fixture", True, ("bash", "-c", "x"), ())
