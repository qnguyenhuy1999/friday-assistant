import sys
import tomllib
from pathlib import Path
from typing import Any


def _load_pyproject() -> dict[str, Any]:
    root = Path(__file__).resolve().parent.parent.parent
    with (root / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)


def test_python_version_matches_pyproject_requirement() -> None:
    data = _load_pyproject()
    requires_python: str = data["project"]["requires-python"]
    assert requires_python.startswith(">=3.13")
    assert sys.version_info >= (3, 13)


def test_repository_root_contains_expected_toolchain_files() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    expected = ("pyproject.toml", "package.json", "pnpm-workspace.yaml", "justfile")
    for name in expected:
        assert (root / name).is_file(), f"missing {name}"
