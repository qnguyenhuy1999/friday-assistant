"""An MCP approval authorizes one binding, not one tool name.

The Phase 18 invariant these tests exist for: an approval granted while a
binding pointed at one server, argv, schema, and risk policy must not survive
a change to any of them. The local alias and the tool input can be byte-for-byte
identical — the approval still must not authorize the call.

That is what makes the approval a decision about *what will actually happen*
rather than about a string the operator once wrote in a config file.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from friday.application.tool_authorization import (
    FINGERPRINT_VERSION,
    compute_authorization_fingerprint,
    find_authorizing_approval,
)
from friday.application.tool_gateway import ToolCall
from friday.domain.approval import ApprovalCategory, ApprovalRequest, ApprovalStatus
from friday.domain.identifiers import ApprovalRequestId, RunId
from friday.domain.json_value import JsonValue
from friday.infrastructure.mcp.bindings import McpBoundTool, compute_binding_fingerprint
from friday.infrastructure.mcp.client import McpClient
from friday.infrastructure.mcp.config import McpServerConfig, McpToolBinding
from friday.infrastructure.mcp.discovery import discover_server
from friday.infrastructure.mcp.schema import normalize_input_schema
from friday.infrastructure.mcp.stdio_client import McpStdioClient
from tests.infrastructure.mcp_fixture_server import make_fixture_server

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
SCHEMA = {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}
CALL = ToolCall(tool="fixture.read", tool_input={"key": "a"})


def _binding(**overrides: object) -> McpToolBinding:
    values: dict[str, object] = {
        "local_name": "fixture.read",
        "remote_tool_name": "read",
        "trusted_description": "Fixture read.",
        "read_only": True,
        "approval_required": True,
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


def _scope(
    server: McpServerConfig | None = None,
    binding: McpToolBinding | None = None,
    schema: JsonValue = None,
    execution_identity: JsonValue | None = None,
) -> str:
    server = server or _server()
    binding = binding or server.bindings[0]
    normalized = normalize_input_schema(SCHEMA if schema is None else schema, max_bytes=4096)
    return McpBoundTool(
        server.server_id,
        binding,
        normalized,
        compute_binding_fingerprint(
            server=server,
            binding=binding,
            normalized_schema=normalized,
            execution_identity=execution_identity,
        ),
    ).authorization_scope


def _approved(run_id: RunId, scope: str) -> ApprovalRequest:
    now = NOW
    approval = ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=run_id,
        category=ApprovalCategory.NETWORK_ACCESS,
        summary="fixture: read",
        reason="external call",
        requested_action="fixture.read",
        requested_input=CALL.tool_input,
        requested_at=now,
        authorization_fingerprint=compute_authorization_fingerprint(
            run_id=run_id, step_id=None, call=CALL, authorization_scope=scope
        ),
    )
    approval.approve(now, "operator")
    return approval


def _authorizes(approval: ApprovalRequest, run_id: RunId, scope: str) -> bool:
    return (
        find_authorizing_approval(
            [approval],
            fingerprint=compute_authorization_fingerprint(
                run_id=run_id, step_id=None, call=CALL, authorization_scope=scope
            ),
        )
        is not None
    )


def test_the_exact_action_fingerprint_binds_the_authorization_scope() -> None:
    assert FINGERPRINT_VERSION == 2
    run_id = RunId.new()
    with_scope = compute_authorization_fingerprint(
        run_id=run_id, step_id=None, call=CALL, authorization_scope=_scope()
    )
    without_scope = compute_authorization_fingerprint(
        run_id=run_id, step_id=None, call=CALL, authorization_scope=None
    )
    assert with_scope != without_scope


def test_an_approval_authorizes_the_binding_it_was_granted_for() -> None:
    run_id, scope = RunId.new(), _scope()
    assert _authorizes(_approved(run_id, scope), run_id, scope) is True


def test_a_consumed_approval_never_authorizes_again() -> None:
    run_id, scope = RunId.new(), _scope()
    approval = _approved(run_id, scope)
    approval.consume(NOW)
    assert _authorizes(approval, run_id, scope) is False


def test_a_pending_approval_never_authorizes() -> None:
    run_id, scope = RunId.new(), _scope()
    now = NOW
    pending = ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=run_id,
        category=ApprovalCategory.NETWORK_ACCESS,
        summary="fixture: read",
        reason="external call",
        requested_action="fixture.read",
        requested_input=CALL.tool_input,
        requested_at=now,
        authorization_fingerprint=compute_authorization_fingerprint(
            run_id=run_id, step_id=None, call=CALL, authorization_scope=scope
        ),
    )
    assert pending.status is ApprovalStatus.PENDING
    assert _authorizes(pending, run_id, scope) is False


def test_rebinding_the_same_local_name_revokes_an_existing_approval() -> None:
    """The heart of the invariant: same alias, same input, different binding.

    Each case below is a way an operator or a compromised server could point
    `fixture.read` somewhere else after a human already said yes.
    """
    run_id = RunId.new()
    granted_scope = _scope()
    approval = _approved(run_id, granted_scope)

    rebound = {
        "another server_id": _scope(server=_server(server_id="other")),
        "another remote tool": _scope(binding=_binding(remote_tool_name="delete")),
        "another argv": _scope(server=_server(command=("other-server",))),
        "another credential set": _scope(server=_server(env_from=("FIXTURE_TOKEN",))),
        "a widened schema": _scope(schema={"type": "object"}),
        "a relaxed risk policy": _scope(binding=_binding(approval_required=False)),
        "another resolved executable": _scope(
            execution_identity={"executable": "/opt/evil/fixture-server"}
        ),
    }
    for reason, scope in rebound.items():
        assert scope != granted_scope, reason
        assert _authorizes(approval, run_id, scope) is False, reason

    # and the original binding still works — this is a fence, not a blanket deny
    assert _authorizes(approval, run_id, granted_scope) is True


def test_an_approval_does_not_cross_runs() -> None:
    scope = _scope()
    approval = _approved(RunId.new(), scope)
    assert _authorizes(approval, RunId.new(), scope) is False


def test_real_credential_rotation_changes_binding_identity_without_leaking_secret(
    tmp_path: Path,
) -> None:
    """The credential principal is opaque, but rotation revokes the grant."""

    def discover(token: str) -> tuple[McpBoundTool, McpClient]:
        server = _server(
            command=make_fixture_server(tmp_path),
            env_from=("FIXTURE_TOKEN",),
        )
        environment = dict(os.environ)
        environment["FIXTURE_TOKEN"] = token
        client = McpStdioClient(server, base_environment=environment)
        result = discover_server(client, server)
        assert len(result.available) == 1
        return result.available[0], client

    first, first_client = discover("value-A")
    try:
        second, second_client = discover("value-B")
        try:
            assert first.binding_fingerprint != second.binding_fingerprint
            run_id = RunId.new()
            approval = _approved(run_id, first.authorization_scope)
            assert _authorizes(approval, run_id, first.authorization_scope)
            assert not _authorizes(approval, run_id, second.authorization_scope)
            durable_identity = str((first.binding_fingerprint, first.provenance, second.provenance))
            assert "value-A" not in durable_identity
            assert "value-B" not in durable_identity
        finally:
            second_client.close()
    finally:
        first_client.close()
