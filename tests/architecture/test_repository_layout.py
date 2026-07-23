"""Structural checks on the repository tree, plus composition-root smoke checks.

Complements test_python_boundaries.py, which checks import direction only.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_DIRECTORY_NAMES = frozenset({"utils", "helpers", "common", "shared"})
IGNORED_PATH_PARTS = frozenset({".git", "node_modules", ".venv"})
TRACKED_SOURCE_ROOTS = ("src", "apps", "packages", "tests")


def _iter_repo_dirs() -> list[Path]:
    dirs: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_dir():
            continue
        if IGNORED_PATH_PARTS.intersection(path.parts):
            continue
        dirs.append(path)
    return dirs


def test_no_generic_dumping_ground_directories() -> None:
    offenders = [str(d) for d in _iter_repo_dirs() if d.name in FORBIDDEN_DIRECTORY_NAMES]
    assert offenders == []


def test_no_python_application_files_at_repository_root() -> None:
    stray = [p.name for p in REPO_ROOT.iterdir() if p.is_file() and p.suffix == ".py"]
    assert stray == []


def test_tracked_source_files_are_not_empty() -> None:
    empty: list[str] = []
    for root_name in TRACKED_SOURCE_ROOTS:
        root = REPO_ROOT / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.stat().st_size == 0:
                empty.append(str(path))
    assert empty == []


def test_friday_package_and_layers_import_successfully() -> None:
    import friday
    import friday.application
    import friday.domain
    import friday.infrastructure

    assert friday.__name__ == "friday"
    assert friday.domain.__name__ == "friday.domain"
    assert friday.application.__name__ == "friday.application"
    assert friday.infrastructure.__name__ == "friday.infrastructure"


def test_api_shell_entry_point_executes() -> None:
    from apps.api.main import main

    assert main() == "Friday Agent OS API shell"


def test_worker_shell_entry_point_executes() -> None:
    from apps.worker.main import main

    assert main() == "Friday Agent OS Worker shell"
