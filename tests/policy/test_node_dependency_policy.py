"""Enforces Phase 3 Node dependency-manifest policy across all package.json
files in the pnpm workspace: root stays private with no production deps,
workspace packages stay private and named @friday/*, packageManager and
engines.node stay pinned/explicit, and no file:/git/http(s) tarball deps.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

MANIFEST_PATHS = (
    "package.json",
    "apps/web/package.json",
    "packages/contracts/package.json",
    "packages/sdk-ts/package.json",
)

FORBIDDEN_DEP_VALUE_PATTERNS = (
    re.compile(r"^file:"),
    re.compile(r"^git(\+\w+)?://"),
    re.compile(r"^https?://"),
)

PACKAGE_MANAGER_PATTERN = re.compile(r"^pnpm@\d+\.\d+\.\d+$")
ENGINES_NODE_PATTERN = re.compile(r"^>=\d+ <\d+$")


def check_manifest(manifest: dict[str, Any], *, is_root: bool) -> list[str]:
    violations: list[str] = []
    name = manifest.get("name", "<unnamed>")

    if manifest.get("private") is not True:
        violations.append(f"{name}: must be private")

    if not is_root:
        if not isinstance(name, str) or not name.startswith("@friday/"):
            violations.append(f"{name}: workspace package name must start with @friday/")

    prod_deps = manifest.get("dependencies")
    if prod_deps:
        violations.append(f"{name}: production dependencies not allowed in Phase 3: {prod_deps}")

    for field in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        deps = manifest.get(field)
        if not isinstance(deps, dict):
            continue
        for dep_name, version in deps.items():
            if isinstance(version, str) and any(
                p.search(version) for p in FORBIDDEN_DEP_VALUE_PATTERNS
            ):
                violations.append(f"{name}: forbidden dependency source {dep_name}={version}")

    if is_root:
        pm = manifest.get("packageManager")
        if not isinstance(pm, str) or not PACKAGE_MANAGER_PATTERN.match(pm):
            violations.append(f"{name}: packageManager must be pinned exactly, got {pm!r}")

        node_range = manifest.get("engines", {}).get("node")
        if not isinstance(node_range, str) or not ENGINES_NODE_PATTERN.match(node_range):
            violations.append(f"{name}: engines.node must be an explicit range, got {node_range!r}")

    return violations


def test_all_real_manifests_are_compliant() -> None:
    violations: dict[str, list[str]] = {}
    for relative_path in MANIFEST_PATHS:
        manifest = json.loads((REPO_ROOT / relative_path).read_text(encoding="utf-8"))
        found = check_manifest(manifest, is_root=(relative_path == "package.json"))
        if found:
            violations[relative_path] = found
    assert violations == {}


def test_detector_flags_production_dependency() -> None:
    manifest = {"name": "@friday/web", "private": True, "dependencies": {"react": "^19.0.0"}}
    violations = check_manifest(manifest, is_root=False)
    assert any("production dependencies" in v for v in violations)


def test_detector_flags_non_private_package() -> None:
    manifest = {"name": "@friday/web", "private": False}
    violations = check_manifest(manifest, is_root=False)
    assert any("must be private" in v for v in violations)


def test_detector_flags_git_url_dependency() -> None:
    manifest = {
        "name": "@friday/web",
        "private": True,
        "devDependencies": {"somepkg": "git+https://example.com/somepkg.git"},
    }
    violations = check_manifest(manifest, is_root=False)
    assert any("forbidden dependency source" in v for v in violations)


def test_detector_flags_unpinned_package_manager() -> None:
    manifest = {
        "name": "friday-agent-os",
        "private": True,
        "packageManager": "pnpm@11",
        "engines": {"node": ">=22 <25"},
    }
    violations = check_manifest(manifest, is_root=True)
    assert any("packageManager must be pinned" in v for v in violations)
