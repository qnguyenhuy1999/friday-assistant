"""Static import-boundary checks for src/friday's layered architecture.

Enforces: friday.domain <- friday.application <- friday.infrastructure.
Each layer may import itself and the layers listed as its allowed imports
below; it must not import any other friday.* layer or any apps.* module.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

LAYER_ALLOWED_IMPORTS: dict[str, frozenset[str]] = {
    "friday.domain": frozenset(),
    "friday.application": frozenset({"friday.domain"}),
    "friday.infrastructure": frozenset({"friday.domain", "friday.application"}),
}

RESTRICTED_PREFIXES = ("friday.", "apps.")


def imported_modules(source: str) -> list[str]:
    """Return every module name a source file imports, via `import` or `from ... import`."""
    tree = ast.parse(source)
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return modules


def _is_within(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def find_violations(layer: str, package_dir: Path) -> list[tuple[Path, str]]:
    """Return (file, forbidden_module) pairs for imports layer is not allowed to make."""
    allowed = LAYER_ALLOWED_IMPORTS[layer]
    violations: list[tuple[Path, str]] = []
    for path in sorted(package_dir.rglob("*.py")):
        for module in imported_modules(path.read_text(encoding="utf-8")):
            if not module.startswith(RESTRICTED_PREFIXES):
                continue
            if _is_within(module, layer):
                continue
            if any(_is_within(module, allowed_prefix) for allowed_prefix in allowed):
                continue
            violations.append((path, module))
    return violations


def test_domain_has_no_outward_dependency() -> None:
    violations = find_violations("friday.domain", REPO_ROOT / "src" / "friday" / "domain")
    assert violations == []


def test_application_depends_only_on_domain() -> None:
    violations = find_violations("friday.application", REPO_ROOT / "src" / "friday" / "application")
    assert violations == []


def test_infrastructure_does_not_depend_on_apps() -> None:
    violations = find_violations(
        "friday.infrastructure", REPO_ROOT / "src" / "friday" / "infrastructure"
    )
    assert violations == []


def test_detector_flags_a_forbidden_domain_import() -> None:
    """Negative fixture: proves the detector actually catches a violation.

    Simulates `friday.domain` importing `friday.infrastructure` without
    touching real source files.
    """
    forbidden_source = "import friday.infrastructure\n"
    allowed = LAYER_ALLOWED_IMPORTS["friday.domain"]
    flagged = [
        module
        for module in imported_modules(forbidden_source)
        if module.startswith(RESTRICTED_PREFIXES)
        and not _is_within(module, "friday.domain")
        and not any(_is_within(module, prefix) for prefix in allowed)
    ]
    assert flagged == ["friday.infrastructure"]
