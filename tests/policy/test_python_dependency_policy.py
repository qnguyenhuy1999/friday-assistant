"""Enforces Python dependency-manifest policy against pyproject.toml.

Rules: [project.dependencies] is allowlisted to sqlalchemy, alembic, fastapi,
uvicorn, and pydantic only (the persistence layer plus the Phase 9 API
delivery boundary); all other quality tooling lives in the dev dependency
group; build-system requirements are exactly pinned; no direct
URL/git/editable/local-path or wildcard/prerelease dependency specifiers.
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
ALLOWED_PROJECT_DEPENDENCY_NAMES = frozenset(
    {"sqlalchemy", "alembic", "fastapi", "uvicorn", "pydantic"}
)


def _load(text: str) -> dict[str, Any]:
    return tomllib.loads(text)


def _dependency_name(spec: str) -> str:
    return re.split(r"[<>=!~;\[\s@]", spec, maxsplit=1)[0].strip()


def _check_specifier_rules(spec: str, source: str) -> list[str]:
    violations: list[str] = []
    if any(pattern.search(spec) for pattern in FORBIDDEN_SPEC_PATTERNS):
        violations.append(f"forbidden direct/URL/local dependency spec ({source}): {spec}")
    if any(marker in spec for marker in WILDCARD_MARKERS):
        violations.append(f"wildcard dependency version ({source}): {spec}")
    if PRERELEASE_MARKERS.search(spec):
        violations.append(f"undocumented prerelease dependency ({source}): {spec}")
    return violations


def check_python_dependency_policy(data: dict[str, Any]) -> list[str]:
    violations: list[str] = []

    project = data.get("project", {})
    for spec in project.get("dependencies", []):
        name = _dependency_name(spec)
        if name not in ALLOWED_PROJECT_DEPENDENCY_NAMES:
            violations.append(
                f"project.dependencies: {name!r} is not an approved production dependency"
            )
        violations.extend(_check_specifier_rules(spec, source="project.dependencies"))

    build_requires = data.get("build-system", {}).get("requires", [])
    for req in build_requires:
        if "==" not in req:
            violations.append(f"build-system requirement not exactly pinned: {req}")

    dev_deps = data.get("dependency-groups", {}).get("dev", [])
    for spec in dev_deps:
        if not isinstance(spec, str):
            continue
        violations.extend(_check_specifier_rules(spec, source="dependency-groups.dev"))

    return violations


def test_real_pyproject_is_compliant() -> None:
    data = _load((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert check_python_dependency_policy(data) == []


def test_detector_flags_non_empty_project_dependencies() -> None:
    data = _load(
        """
        [project]
        dependencies = ["requests"]
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


def test_project_dependencies_allow_only_sqlalchemy_and_alembic() -> None:
    data = _load((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    violations = check_python_dependency_policy(data)
    assert violations == []


def test_detector_flags_an_unlisted_project_dependency() -> None:
    data = {"project": {"dependencies": ["sqlalchemy>=2.0.51", "requests>=2.0.0"]}}
    violations = check_python_dependency_policy(data)
    assert any("requests" in v for v in violations)


def test_detector_flags_a_wildcard_project_dependency() -> None:
    data = {"project": {"dependencies": ["sqlalchemy==*"]}}
    violations = check_python_dependency_policy(data)
    assert any("wildcard" in v for v in violations)
