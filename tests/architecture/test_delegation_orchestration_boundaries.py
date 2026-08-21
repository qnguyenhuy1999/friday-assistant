"""Delegation orchestration may create Friday work, never execute authority.

The delegation use cases and reconciler are control-plane orchestration.  They
may create durable Tasks/Runs/work items and consume durable Friday results,
but they must never directly depend on tool gateways, provider adapters,
subprocess/shell primitives, or approval execution.  Delegated Runs reach those
capabilities only through the ordinary AgentRunProcessor authority path.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

MODULES = (
    ROOT / "src/friday/application/delegation.py",
    ROOT / "src/friday/application/delegation_reconciliation.py",
)

FORBIDDEN_SUBSTRINGS = (
    "ToolGateway",
    "tool_gateway",
    "WorkspaceToolGateway",
    "workspace_files",
    "McpToolGateway",
    "mcp_gateway",
    "mcp_composition",
    "mcp_stdio",
    "ComputerToolGateway",
    "computer_gateway",
    "computer_composition",
    "cua_driver",
    "message_tool_gateway",
    "messaging.dispatcher",
    "webhook_transport",
    "claude_cli",
    "anthropic",
    "openai",
    "codex",
    "opencode",
    "hermes",
    "provider_adapter",
    "subprocess",
    "os.system",
    "ApproveRequest",
    "RejectRequest",
    "approval_workflow",
    "RequestToolApproval",
    "tool_authorization",
    "ExecuteToolAction",
    "claim_aware_tool_execution",
)


def _imported_names(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    return [node.module or "", *(alias.name for alias in node.names)]


def test_delegation_orchestration_has_no_tool_provider_shell_or_approval_dependency() -> None:
    offenders: list[str] = []
    for path in MODULES:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            for imported in _imported_names(node):
                hit = next(
                    (forbidden for forbidden in FORBIDDEN_SUBSTRINGS if forbidden in imported),
                    None,
                )
                if hit is not None:
                    offenders.append(
                        f"{path.relative_to(ROOT)}: {imported!r} matches {hit!r}"
                    )
    assert offenders == []


def test_delegation_orchestration_never_calls_subprocess_or_os_system() -> None:
    offenders: list[str] = []
    for path in MODULES:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if isinstance(node.value, ast.Name) and node.value.id in {"subprocess", "os"}:
                offenders.append(f"{path.relative_to(ROOT)}: {node.value.id}.{node.attr}")
    assert offenders == []
