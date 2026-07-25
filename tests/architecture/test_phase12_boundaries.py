"""Phase 12 memory boundaries: vaults and Graphify stay behind infrastructure."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.architecture.test_python_boundaries import imported_modules

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (REPO_ROOT / "src", REPO_ROOT / "apps")
DOMAIN_ROOT = REPO_ROOT / "src" / "friday" / "domain"
APPLICATION_ROOT = REPO_ROOT / "src" / "friday" / "application"
INFRASTRUCTURE_MEMORY_ROOT = REPO_ROOT / "src" / "friday" / "infrastructure" / "memory"

_FORBIDDEN_DEPENDENCIES = (
    "playwright",
    "selenium",
    "computer_use",
    "chromedriver",
    "faiss",
    "chromadb",
    "pinecone",
    "weaviate",
    "qdrant",
    "milvus",
    "sentence_transformers",
    "openai",
)


def _python_files(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(root.rglob("*.py")))


def _imports(path: Path) -> tuple[str, ...]:
    return tuple(imported_modules(path.read_text(encoding="utf-8")))


def _source_tree_files() -> tuple[Path, ...]:
    return tuple(path for root in SOURCE_ROOTS for path in _python_files(root))


def test_domain_has_no_obsidian_or_graphify_dependency() -> None:
    offenders = [
        f"{path}: {module}"
        for path in _python_files(DOMAIN_ROOT)
        for module in _imports(path)
        if "obsidian" in module.lower() or "graphify" in module.lower()
    ]
    assert offenders == []


def test_application_has_no_subprocess_or_graphify_dependency() -> None:
    offenders = [
        f"{path}: {module}"
        for path in _python_files(APPLICATION_ROOT)
        for module in _imports(path)
        if module.split(".")[0] == "subprocess" or "graphify" in module.lower()
    ]
    assert offenders == []


def test_graphify_json_handling_lives_only_in_memory_infrastructure() -> None:
    offenders: list[str] = []
    for path in _source_tree_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        mentions_graph_shape = any(
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and ("built_at_commit" in node.value or "hyperedges" in node.value)
            for node in ast.walk(tree)
        )
        if mentions_graph_shape and not path.is_relative_to(INFRASTRUCTURE_MEMORY_ROOT):
            offenders.append(str(path))
    assert offenders == []


def test_claude_adapter_neither_reads_vault_nor_invokes_graphify() -> None:
    adapter = REPO_ROOT / "src" / "friday" / "infrastructure" / "brain" / "claude_cli.py"
    source = adapter.read_text(encoding="utf-8").lower()

    assert "obsidian" not in source
    assert "graphify" not in source
    assert "vault" not in source


def test_only_infrastructure_memory_owns_graphify_named_modules() -> None:
    offenders = [
        str(path)
        for path in _source_tree_files()
        if "graphify" in path.name.lower() and not path.is_relative_to(INFRASTRUCTURE_MEMORY_ROOT)
    ]
    assert offenders == []


def test_no_computer_use_vector_database_or_embedding_sdk_was_added() -> None:
    offenders = [
        f"{path}: {module}"
        for path in _source_tree_files()
        for module in _imports(path)
        if module.split(".")[0].lower() in _FORBIDDEN_DEPENDENCIES
    ]
    dependency_text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    declared = [name for name in _FORBIDDEN_DEPENDENCIES if name in dependency_text]

    assert offenders == []
    assert declared == []
