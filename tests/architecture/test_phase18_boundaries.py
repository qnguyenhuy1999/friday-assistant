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


def test_the_brain_runtime_passes_no_mcp_configuration_to_claude() -> None:
    source = (REPO_ROOT / "src/friday/infrastructure/brain/claude_cli.py").read_text(
        encoding="utf-8"
    )
    forbidden = (
        "--mcp-config",
        "--mcp-server",
        "mcpServers",
        "--dangerously-skip-permissions",
        "--permission-mode",
        "--allowedTools",
    )
    assert [flag for flag in forbidden if flag in source] == []


def test_exactly_one_module_implements_mcp_stdio_framing() -> None:
    """Both MCP consumers — computer use and the tool gateway — must sit on the
    same transport. Two framing implementations means two places to get line
    bounds, queue bounds, and handshake cleanup right, and only one of them
    stays correct.
    """
    session = REPO_ROOT / "src/friday/infrastructure/process/stdio_jsonrpc.py"
    spawners = [
        path
        for root in (
            REPO_ROOT / "src/friday/infrastructure/mcp",
            REPO_ROOT / "src/friday/infrastructure/computer",
        )
        for path in root.rglob("*.py")
        if "subprocess.Popen(" in path.read_text(encoding="utf-8")
    ]
    assert spawners == [], [str(path.relative_to(REPO_ROOT)) for path in spawners]
    assert "subprocess.Popen(" in session.read_text(encoding="utf-8")

    for adapter in (
        REPO_ROOT / "src/friday/infrastructure/mcp/stdio_client.py",
        REPO_ROOT / "src/friday/infrastructure/computer/mcp_stdio.py",
    ):
        assert "friday.infrastructure.process.stdio_jsonrpc" in set(
            imported_modules(adapter.read_text(encoding="utf-8"))
        ), adapter.name


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
