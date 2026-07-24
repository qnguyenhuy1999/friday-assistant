"""Phase 9 API delivery boundary checks, complementing test_python_boundaries.py.

Enforces: FastAPI/Pydantic/Starlette/Uvicorn stay confined to `apps.api`;
`apps.api.routes.*` modules never import SQLAlchemy directly (persistence
failures are translated to `ApplicationError` inside the `UnitOfWork`
boundary, and connectivity checks live in `friday.infrastructure`).
"""

from __future__ import annotations

from pathlib import Path

from tests.architecture.test_python_boundaries import imported_modules

REPO_ROOT = Path(__file__).resolve().parents[2]

DELIVERY_FRAMEWORK_PREFIXES = ("fastapi", "pydantic", "starlette", "uvicorn")


def _framework_imports(package_dir: Path) -> list[tuple[Path, str]]:
    violations: list[tuple[Path, str]] = []
    for path in sorted(package_dir.rglob("*.py")):
        for module in imported_modules(path.read_text(encoding="utf-8")):
            if module.startswith(DELIVERY_FRAMEWORK_PREFIXES):
                violations.append((path, module))
    return violations


def test_domain_has_no_delivery_framework_import() -> None:
    violations = _framework_imports(REPO_ROOT / "src" / "friday" / "domain")
    assert violations == []


def test_application_has_no_delivery_framework_import() -> None:
    violations = _framework_imports(REPO_ROOT / "src" / "friday" / "application")
    assert violations == []


def test_infrastructure_has_no_delivery_framework_import() -> None:
    violations = _framework_imports(REPO_ROOT / "src" / "friday" / "infrastructure")
    assert violations == []


def test_routes_do_not_import_sqlalchemy() -> None:
    routes_dir = REPO_ROOT / "apps" / "api" / "routes"
    violations: list[tuple[Path, str]] = []
    for path in sorted(routes_dir.rglob("*.py")):
        for module in imported_modules(path.read_text(encoding="utf-8")):
            if module.startswith("sqlalchemy"):
                violations.append((path, module))
    assert violations == []
