from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.worker.mcp_settings import McpSettings
from friday.infrastructure.tools.mcp_composition import McpConfigurationInvalid


def test_mcp_settings_default_off_and_parse_strict_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert McpSettings.from_env().gateway_config().enabled is False
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "server_id": "fixture",
                        "command": ["fixture"],
                        "bindings": [
                            {
                                "local_name": "fixture.read",
                                "remote_tool_name": "read",
                                "trusted_description": "Read.",
                                "read_only": True,
                                "approval_required": False,
                                "approval_category": "network_access",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FRIDAY_MCP_ENABLED", "true")
    monkeypatch.setenv("FRIDAY_MCP_CONFIG_PATH", str(path))
    assert McpSettings.from_env().gateway_config().servers[0].server_id == "fixture"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(McpConfigurationInvalid):
        McpSettings.from_env().gateway_config()


@pytest.mark.parametrize("constant", ["NaN", "Infinity"])
def test_non_standard_json_constants_are_configuration_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, constant: str
) -> None:
    path = tmp_path / "mcp.json"
    path.write_text('{"servers": ' + constant + "}", encoding="utf-8")
    monkeypatch.setenv("FRIDAY_MCP_ENABLED", "true")
    monkeypatch.setenv("FRIDAY_MCP_CONFIG_PATH", str(path))

    with pytest.raises(McpConfigurationInvalid):
        McpSettings.from_env().gateway_config()
