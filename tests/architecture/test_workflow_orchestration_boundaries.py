"""Workflow orchestration (workflow_execution_use_cases.py, workflow_registry.py)
must never directly depend on or invoke a tool gateway, a provider/BrainRuntime
adapter, a subprocess/shell primitive, or direct approval execution. It may
only create durable Tasks/Runs/work items and inspect durable outcomes --
Workflow child Runs execute through the normal Friday authority path
(AgentRunProcessor, ResolveRunAgent, approval/tool lifecycle), not through a
shortcut taken by the orchestrator itself.

Checks both `import x` and `from x import y` forms, and matches on the
imported module path AND the imported symbol names -- a substring match on
just one side would miss e.g. `from friday.application.tool_gateway import
ToolGateway` (module path is the lowercase filename; the forbidden symbol
name is the thing actually imported).
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

MODULES = (
    ROOT / "src/friday/application/workflow_execution_use_cases.py",
    ROOT / "src/friday/application/workflow_registry.py",
)

# Substrings matched case-sensitively against both the dotted module path of
# an import and every symbol name imported from it.
FORBIDDEN_SUBSTRINGS = (
    # Tool gateways -- Workflow orchestration must never invoke a tool itself.
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
    "process_runner",
    # Concrete provider adapters -- orchestration dispatches ordinary
    # Tasks/Runs and only checks runtime *registration* (BrainRuntimeRegistry
    # is a legitimate, non-invoking dependency and is deliberately not
    # forbidden here), it never talks to a provider directly.
    "claude_cli",
    "anthropic",
    "openai",
    # Subprocess/shell.
    "subprocess",
    "os.system",
    # Direct approval execution -- orchestration must not resolve or execute
    # approvals itself; that belongs to the ordinary child-Run authority path.
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


def test_workflow_orchestration_has_no_tool_provider_or_shell_dependency() -> None:
    offenders: list[str] = []
    for path in MODULES:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            for imported in _imported_names(node):
                hit = next((f for f in FORBIDDEN_SUBSTRINGS if f in imported), None)
                if hit is not None:
                    offenders.append(f"{path.relative_to(ROOT)}: {imported!r} matches {hit!r}")
    assert offenders == []


def test_workflow_orchestration_never_calls_subprocess_or_os_system() -> None:
    """Belt-and-suspenders: even a dynamically-imported subprocess call
    (`import subprocess` inside a function, `getattr(os, "system")`, etc.)
    would show up as a bare-name `subprocess`/`os` attribute access."""
    offenders: list[str] = []
    for path in MODULES:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if isinstance(node.value, ast.Name) and node.value.id in {"subprocess", "os"}:
                offenders.append(f"{path.relative_to(ROOT)}: {node.value.id}.{node.attr}")
    assert offenders == []
