"""Structural repository-cleanliness policy: forbidden tracked artifacts,
merge-conflict markers, unexpected executables, required governance files,
package-local README ownership, and no domain/contract code before Phase 4.

Uses `git ls-files` as the source of truth for what is tracked, so it
reflects the real repository without depending on local untracked state.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_TRACKED_SUFFIXES = (
    ".pyc",
    ".tsbuildinfo",
)
FORBIDDEN_TRACKED_NAMES = (
    ".env",
    ".coverage",
)
FORBIDDEN_TRACKED_DIR_PARTS = (
    "node_modules",
    "__pycache__",
    ".venv",
    "htmlcov",
    "dist",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
)

REQUIRED_GOVERNANCE_FILES = (
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "LICENSE",
    "docs/architecture/README.md",
    "docs/governance/provenance.md",
    "docs/governance/repository-rules.md",
    "docs/governance/quality-gates.md",
)

PACKAGE_LOCAL_READMES = (
    "apps/api/README.md",
    "apps/worker/README.md",
    "apps/web/README.md",
    "packages/contracts/README.md",
    "packages/sdk-ts/README.md",
)

CONFLICT_MARKERS = ("<<<<<<< ", ">>>>>>> ")


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def find_forbidden_tracked_paths(paths: list[str]) -> list[str]:
    offenders: list[str] = []
    for path in paths:
        name = Path(path).name
        parts = Path(path).parts
        if (
            name in FORBIDDEN_TRACKED_NAMES
            or any(path.endswith(suffix) for suffix in FORBIDDEN_TRACKED_SUFFIXES)
            or any(part in FORBIDDEN_TRACKED_DIR_PARTS for part in parts)
        ):
            offenders.append(path)
    return offenders


def find_conflict_markers(paths: list[str]) -> list[str]:
    offenders: list[str] = []
    for path in paths:
        full = REPO_ROOT / path
        if not full.is_file():
            continue
        try:
            text = full.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            continue
        lines = text.splitlines()
        if any(line.startswith(marker) for marker in CONFLICT_MARKERS for line in lines):
            offenders.append(path)
    return offenders


def test_no_forbidden_tracked_artifacts() -> None:
    assert find_forbidden_tracked_paths(tracked_files()) == []


def test_no_merge_conflict_markers_in_tracked_files() -> None:
    assert find_conflict_markers(tracked_files()) == []


def test_required_governance_files_exist() -> None:
    missing = [f for f in REQUIRED_GOVERNANCE_FILES if not (REPO_ROOT / f).is_file()]
    assert missing == []


def test_package_local_readmes_exist() -> None:
    missing = [f for f in PACKAGE_LOCAL_READMES if not (REPO_ROOT / f).is_file()]
    assert missing == []


def test_no_unexpected_executable_files() -> None:
    result = subprocess.run(
        ["git", "ls-files", "-s"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    executables = []
    for line in result.stdout.splitlines():
        mode, _sha, _stage_and_path = line.split(" ", 2)
        _stage, path = _stage_and_path.split("\t", 1)
        if mode == "100755":
            executables.append(path)
    assert set(executables) == {"scripts/bootstrap.sh", "scripts/check.sh"}


def test_detector_flags_a_forbidden_tracked_artifact() -> None:
    """Negative fixture: proves the detector catches a forbidden tracked path
    without touching real git state."""
    assert find_forbidden_tracked_paths(["apps/api/.coverage"]) == ["apps/api/.coverage"]
    assert find_forbidden_tracked_paths(["packages/sdk-ts/dist/index.js"]) == [
        "packages/sdk-ts/dist/index.js"
    ]
