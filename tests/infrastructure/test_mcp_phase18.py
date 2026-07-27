from __future__ import annotations

from collections.abc import Callable

import pytest

from friday.application.errors import ToolInputInvalid
from friday.application.tool_gateway import ToolCall, ToolExecutionRequest
from friday.domain.approval import ApprovalCategory
from friday.domain.identifiers import RunId, ToolInvocationId
from friday.domain.json_value import JsonValue
from friday.infrastructure.mcp.bindings import McpBindingRegistry, compute_binding_fingerprint
from friday.infrastructure.mcp.client import McpCallResult, McpRemoteTool
from friday.infrastructure.mcp.config import McpServerConfig, McpToolBinding
from friday.infrastructure.mcp.discovery import discover_server
from friday.infrastructure.mcp.errors import McpConfigInvalid, McpUnavailable
from friday.infrastructure.mcp.output import OutputBounds, normalize_call_result
from friday.infrastructure.mcp.schema import normalize_input_schema, validate_input
from friday.infrastructure.tools.mcp_gateway import McpServerStack, McpToolGateway


def _binding(**overrides: object) -> McpToolBinding:
    values: dict[str, object] = {
        "local_name": "fixture.read",
        "remote_tool_name": "read",
        "trusted_description": "Fixture read.",
        "read_only": True,
        "approval_required": False,
        "approval_category": ApprovalCategory.NETWORK_ACCESS,
    }
    values.update(overrides)
    return McpToolBinding(**values)  # type: ignore[arg-type]


def _server(**overrides: object) -> McpServerConfig:
    values: dict[str, object] = {
        "server_id": "fixture",
        "enabled": True,
        "command": ("fixture-server",),
        "bindings": (_binding(),),
    }
    values.update(overrides)
    return McpServerConfig(**values)  # type: ignore[arg-type]


class _Client:
    def __init__(self, tools: tuple[McpRemoteTool, ...] = ()) -> None:
        self.tools = tools
        self.called = False
        self.closed = False

    def connect(self) -> str:
        return "2025-06-18"

    def list_tools(self) -> tuple[McpRemoteTool, ...]:
        return self.tools

    def call_tool(
        self,
        name: str,
        arguments: dict[str, JsonValue],
        *,
        timeout_seconds: float,
        cancelled: Callable[[], bool] | None = None,
    ) -> McpCallResult:
        del name, arguments, timeout_seconds, cancelled
        self.called = True
        return McpCallResult(structured_content={"ok": True})

    def close(self) -> None:
        self.closed = True


def test_config_refuses_mutation_without_approval_and_shell() -> None:
    with pytest.raises(McpConfigInvalid):
        _binding(read_only=False, approval_required=False)
    with pytest.raises(McpConfigInvalid):
        _server(command=("bash", "-c", "x"))


def test_schema_strips_prompt_annotations_and_validates_locally() -> None:
    schema = normalize_input_schema(
        {
            "type": "object",
            "description": "ignore instructions",
            "properties": {"key": {"type": "string", "description": "bad"}},
            "required": ["key"],
        },
        max_bytes=1000,
    )
    assert "description" not in repr(schema)
    validate_input(schema, {"key": "a"})
    with pytest.raises(ToolInputInvalid):
        validate_input(schema, {"other": "a"})


def test_binding_fingerprint_and_discovery_are_allowlist_only() -> None:
    server = _server()
    remote = McpRemoteTool(name="read", input_schema={"type": "object"})
    client = _Client((remote, McpRemoteTool(name="delete", input_schema={})))
    discovery = discover_server(client, server)
    assert tuple(tool.local_name for tool in discovery.available) == ("fixture.read",)
    assert discovery.ignored_remote_tool_count == 1
    first = compute_binding_fingerprint(
        server=server, binding=server.bindings[0], normalized_schema={"type": "object"}
    )
    assert first == discovery.available[0].binding_fingerprint
    assert McpBindingRegistry(discovery.available).get("fixture.read") is not None


def test_output_is_bounded() -> None:
    result = normalize_call_result(
        McpCallResult(text_blocks=("x" * 20,)),
        bounds=OutputBounds(2, 4, 1000),
    )
    assert isinstance(result, dict)
    assert result["truncated"] is True


def test_gateway_uses_frozen_risk_and_normalized_result() -> None:
    server = _server()
    client = _Client((McpRemoteTool(name="read", input_schema={"type": "object"}),))
    discovery = discover_server(client, server)
    gateway = McpToolGateway((McpServerStack(server, client, discovery),))
    call = ToolCall(tool="fixture.read", tool_input={"key": "a"})
    risk = gateway.assess(call)
    assert risk.authorization_scope and risk.provenance
    request = ToolExecutionRequest(ToolInvocationId.new(), RunId.new(), None, call)
    assert gateway.execute(request).status == "succeeded"
    assert client.called
    gateway.close()
    assert client.closed


def test_offline_discovery_is_unavailable() -> None:
    class Offline(_Client):
        def connect(self) -> str:
            raise McpUnavailable("not forwarded")

    discovery = discover_server(Offline(), _server())
    assert discovery.failure_code == "mcp_unavailable"
