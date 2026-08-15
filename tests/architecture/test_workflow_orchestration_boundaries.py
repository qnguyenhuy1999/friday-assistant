import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = (
    "ToolGateway",
    "provider_adapters",
    "subprocess",
    "claude_cli",
    "openai",
    "anthropic",
)
MODULES = (
    ROOT / "src/friday/application/workflow_execution_use_cases.py",
    ROOT / "src/friday/application/workflow_registry.py",
)


def test_workflow_orchestration_has_no_provider_or_tool_adapter_dependency() -> None:
    offenders = []
    for path in MODULES:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(any(part in name for part in FORBIDDEN) for name in names):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
