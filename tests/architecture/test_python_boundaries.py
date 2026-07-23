"""Static import-boundary checks for src/friday's layered architecture.

Enforces: friday.domain <- friday.application <- friday.infrastructure.
Each layer may import itself and the layers listed as its allowed imports
below; it must not import any other friday.* layer or any apps.* module.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

LAYER_ALLOWED_IMPORTS: dict[str, frozenset[str]] = {
    "friday.domain": frozenset(),
    "friday.application": frozenset({"friday.domain"}),
    "friday.infrastructure": frozenset({"friday.domain", "friday.application"}),
}

RESTRICTED_PREFIXES = ("friday.", "apps.")

# Permanent rule (Phase 4): the domain must stay standard-library only — no
# Pydantic, jsonschema, ORM, or any other third-party dependency. `__future__`
# is a stdlib pseudo-module; `friday` itself covers intra-domain imports.
STDLIB_MODULE_NAMES = frozenset(sys.stdlib_module_names) | {"__future__"}


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


def find_non_stdlib_imports(package_dir: Path) -> list[tuple[Path, str]]:
    """Return (file, module) pairs for any top-level import that is neither
    standard-library nor within `friday.*` — i.e. any third-party dependency."""
    violations: list[tuple[Path, str]] = []
    for path in sorted(package_dir.rglob("*.py")):
        for module in imported_modules(path.read_text(encoding="utf-8")):
            top_level = module.split(".", 1)[0]
            if top_level == "friday" or top_level in STDLIB_MODULE_NAMES:
                continue
            violations.append((path, module))
    return violations


def test_domain_has_no_outward_dependency() -> None:
    violations = find_violations("friday.domain", REPO_ROOT / "src" / "friday" / "domain")
    assert violations == []


def test_domain_uses_only_standard_library() -> None:
    violations = find_non_stdlib_imports(REPO_ROOT / "src" / "friday" / "domain")
    assert violations == []


def test_detector_flags_a_non_stdlib_domain_import(tmp_path: Path) -> None:
    """Negative fixture: proves the stdlib-only detector catches a real
    third-party import, using a synthetic temp package rather than a real
    domain file."""
    fixture_dir = tmp_path / "domain"
    fixture_dir.mkdir()
    (fixture_dir / "bad.py").write_text("import jsonschema\n", encoding="utf-8")
    violations = find_non_stdlib_imports(fixture_dir)
    assert violations == [(fixture_dir / "bad.py", "jsonschema")]


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
