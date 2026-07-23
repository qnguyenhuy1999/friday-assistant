"""Enforces Phase 3 Python dependency-manifest policy against pyproject.toml.

Rules: [project.dependencies] stays empty; all quality tooling lives in the
dev dependency group; build-system requirements are exactly pinned; no
direct URL/git/editable/local-path or wildcard/prerelease dependency
specifiers.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_SPEC_PATTERNS = (
    re.compile(r"@\s*git\+"),
    re.compile(r"@\s*https?://"),
    re.compile(r"@\s*file://"),
    re.compile(r"^\s*\.\.?/"),
)
PRERELEASE_MARKERS = re.compile(r"\d(a|b|rc)\d|\.dev\d|\.post\d")
WILDCARD_MARKERS = ("*",)


def _load(text: str) -> dict[str, Any]:
    return tomllib.loads(text)


def check_python_dependency_policy(data: dict[str, Any]) -> list[str]:
    violations: list[str] = []

    project = data.get("project", {})
    if project.get("dependencies"):
        violations.append("project.dependencies must remain empty in Phase 3")

    build_requires = data.get("build-system", {}).get("requires", [])
    for req in build_requires:
        if "==" not in req:
            violations.append(f"build-system requirement not exactly pinned: {req}")

    dev_deps = data.get("dependency-groups", {}).get("dev", [])
    for spec in dev_deps:
        if not isinstance(spec, str):
            continue
        if any(pattern.search(spec) for pattern in FORBIDDEN_SPEC_PATTERNS):
            violations.append(f"forbidden direct/URL/local dependency spec: {spec}")
        if any(marker in spec for marker in WILDCARD_MARKERS):
            violations.append(f"wildcard dependency version: {spec}")
        if PRERELEASE_MARKERS.search(spec):
            violations.append(f"undocumented prerelease dependency: {spec}")

    return violations


def test_real_pyproject_is_compliant() -> None:
    data = _load((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert check_python_dependency_policy(data) == []


def test_detector_flags_non_empty_project_dependencies() -> None:
    data = _load(
        """
        [project]
        dependencies = ["fastapi"]
        """
    )
    violations = check_python_dependency_policy(data)
    assert any("project.dependencies" in v for v in violations)


def test_detector_flags_unpinned_build_requirement() -> None:
    data = _load(
        """
        [build-system]
        requires = ["hatchling>=1.0"]
        """
    )
    violations = check_python_dependency_policy(data)
    assert any("not exactly pinned" in v for v in violations)


def test_detector_flags_direct_git_dependency() -> None:
    data = _load(
        """
        [dependency-groups]
        dev = ["mypkg @ git+https://example.com/mypkg.git"]
        """
    )
    violations = check_python_dependency_policy(data)
    assert any("forbidden direct/URL/local" in v for v in violations)


def test_detector_flags_wildcard_dependency() -> None:
    data = _load(
        """
        [dependency-groups]
        dev = ["somepkg==*"]
        """
    )
    violations = check_python_dependency_policy(data)
    assert any("wildcard" in v for v in violations)


def test_detector_flags_prerelease_dependency() -> None:
    data = _load(
        """
        [dependency-groups]
        dev = ["somepkg==1.0.0rc1"]
        """
    )
    violations = check_python_dependency_policy(data)
    assert any("prerelease" in v for v in violations)
