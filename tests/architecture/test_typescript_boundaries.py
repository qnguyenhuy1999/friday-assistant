"""Static dependency-boundary checks for the pnpm TypeScript workspaces.

package.json manifests are the source of truth for allowed inter-package
dependencies. tsc's project-reference graph does not fail merely because a
manifest declares a disallowed workspace dependency that nothing actually
imports yet, so this is checked independently of typechecking.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

DEPENDENCY_FIELDS = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)

ALLOWED_INTERNAL_DEPENDENCIES: dict[str, frozenset[str]] = {
    "@friday/web": frozenset({"@friday/contracts", "@friday/sdk"}),
    "@friday/sdk": frozenset({"@friday/contracts"}),
    "@friday/contracts": frozenset(),
}

WORKSPACE_PACKAGE_JSON_PATHS = (
    "apps/web/package.json",
    "packages/contracts/package.json",
    "packages/sdk-ts/package.json",
)


def internal_dependencies(manifest: dict[str, Any]) -> set[str]:
    """Return every @friday/* name this manifest declares, across all dependency fields."""
    found: set[str] = set()
    for field in DEPENDENCY_FIELDS:
        deps = manifest.get(field)
        if not isinstance(deps, dict):
            continue
        found.update(name for name in deps if isinstance(name, str) and name.startswith("@friday/"))
    return found


def find_violations(manifest: dict[str, Any]) -> set[str]:
    """Return declared @friday/* dependencies this manifest is not allowed to have."""
    name = manifest.get("name")
    if not isinstance(name, str) or name not in ALLOWED_INTERNAL_DEPENDENCIES:
        return set()
    allowed = ALLOWED_INTERNAL_DEPENDENCIES[name]
    return {dep for dep in internal_dependencies(manifest) if dep != name and dep not in allowed}


def test_no_workspace_package_declares_a_forbidden_internal_dependency() -> None:
    violations: dict[str, set[str]] = {}
    for relative_path in WORKSPACE_PACKAGE_JSON_PATHS:
        manifest = json.loads((REPO_ROOT / relative_path).read_text(encoding="utf-8"))
        found = find_violations(manifest)
        if found:
            violations[relative_path] = found
    assert violations == {}


def test_detector_flags_a_forbidden_contracts_dependency_on_web() -> None:
    """Negative fixture: proves the detector catches a forbidden manifest edge.

    Simulates packages/contracts/package.json declaring a dependency on
    @friday/web, without touching any real file.
    """
    forbidden_manifest: dict[str, Any] = {
        "name": "@friday/contracts",
        "dependencies": {"@friday/web": "workspace:*"},
    }
    assert find_violations(forbidden_manifest) == {"@friday/web"}
