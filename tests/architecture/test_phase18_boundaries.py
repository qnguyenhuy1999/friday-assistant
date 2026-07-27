from __future__ import annotations

import ast
from pathlib import Path

from tests.architecture.test_python_boundaries import imported_modules

REPO_ROOT = Path(__file__).resolve().parents[2]
MCP_PREFIX = "friday.infrastructure.mcp"
AUTHORIZED = {
    REPO_ROOT / "src/friday/infrastructure/tools/mcp_gateway.py",
    REPO_ROOT / "src/friday/infrastructure/tools/mcp_composition.py",
    REPO_ROOT / "apps/worker/mcp_settings.py",
}


def test_only_mcp_substrate_and_bridges_import_mcp() -> None:
    offenders: list[str] = []
    for root in (REPO_ROOT / "src", REPO_ROOT / "apps"):
        for path in root.rglob("*.py"):
            if "src/friday/infrastructure/mcp/" in str(path):
                continue
            imports = imported_modules(path.read_text(encoding="utf-8"))
            if path not in AUTHORIZED and any(
                module == MCP_PREFIX or module.startswith(f"{MCP_PREFIX}.") for module in imports
            ):
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


def test_brain_runtime_has_no_mcp_awareness() -> None:
    path = REPO_ROOT / "src/friday/infrastructure/brain/claude_cli.py"
    assert not any(module.startswith(MCP_PREFIX) for module in imported_modules(path.read_text()))


def test_no_source_uses_shell_true_or_generic_mcp_dispatcher() -> None:
    for root in (REPO_ROOT / "src", REPO_ROOT / "apps"):
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
            assert not any(
                isinstance(node, ast.Call)
                and any(
                    keyword.arg == "shell"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                    for keyword in node.keywords
                )
                for node in ast.walk(tree)
            )
            assert '"mcp.call"' not in text and "'mcp.call'" not in text
