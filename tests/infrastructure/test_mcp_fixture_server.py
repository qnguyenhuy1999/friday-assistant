from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.infrastructure.mcp_fixture_server import FIXTURE_TOOLS, make_fixture_server


def test_fixture_completes_mcp_handshake_and_lists_tools(tmp_path: Path) -> None:
    payload = (
        "\n".join(
            (
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
            )
        )
        + "\n"
    )
    completed = subprocess.run(
        list(make_fixture_server(tmp_path)),
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    replies = [json.loads(line) for line in completed.stdout.splitlines()]
    assert tuple(tool["name"] for tool in replies[1]["result"]["tools"]) == FIXTURE_TOOLS
