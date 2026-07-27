"""File-hygiene policy: tracked files parse, stay within the size limit, end
with a newline, and carry no trailing whitespace.

These invariants used to reach CI only through `just pre-commit`, whose
upstream `check-toml`, `check-added-large-files`, `trailing-whitespace`, and
`end-of-file-fixer` hooks are not wrapped by any `just` recipe. CI now runs
`just check` instead, so the invariants are asserted here as non-mutating
detectors that run anywhere `pytest` does -- unlike the hooks, which rewrite
files rather than reporting on them.

Deliberately not re-checked here:

- YAML and JSON parseability. `just format-check` runs `prettier --check` over
  every tracked `.yaml`/`.yml`/`.json` file, which cannot format a file it
  cannot parse. The one prettier-ignored lockfile, `pnpm-lock.yaml`, is parsed
  by the frozen install in `just lock-check`.
- Merge-conflict markers -- see `test_repository_policy.py`.
- Private-key contents -- see `test_sensitive_files.py`.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Mirrors the `--maxkb=1024` argument on the pre-commit
# `check-added-large-files` hook. Applies to every tracked file, not just newly
# added ones, so history cannot drift past the limit unnoticed.
MAX_TRACKED_FILE_KB = 1024


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _read_text(path: Path) -> str | None:
    """Returns None for anything that is not decodable UTF-8 text, so binary
    files are skipped rather than reported as violations."""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, ValueError):
        return None


def find_oversized_paths(paths: list[str], root: Path = REPO_ROOT) -> list[str]:
    offenders = []
    for path in paths:
        full = root / path
        if full.is_file() and full.stat().st_size > MAX_TRACKED_FILE_KB * 1024:
            offenders.append(path)
    return offenders


def find_unparsable_toml(paths: list[str], root: Path = REPO_ROOT) -> list[str]:
    offenders = []
    for path in paths:
        full = root / path
        if not full.is_file() or full.suffix != ".toml":
            continue
        try:
            with full.open("rb") as handle:
                tomllib.load(handle)
        except tomllib.TOMLDecodeError:
            offenders.append(path)
    return offenders


def find_missing_final_newline(paths: list[str], root: Path = REPO_ROOT) -> list[str]:
    offenders = []
    for path in paths:
        full = root / path
        if not full.is_file():
            continue
        text = _read_text(full)
        # An empty file has no missing newline to report.
        if text and not text.endswith("\n"):
            offenders.append(path)
    return offenders


def find_trailing_whitespace(paths: list[str], root: Path = REPO_ROOT) -> list[str]:
    offenders = []
    for path in paths:
        full = root / path
        if not full.is_file():
            continue
        text = _read_text(full)
        if text is None:
            continue
        if any(line != line.rstrip() for line in text.splitlines()):
            offenders.append(path)
    return offenders


def test_no_tracked_file_exceeds_the_size_limit() -> None:
    assert find_oversized_paths(tracked_files()) == []


def test_every_tracked_toml_file_parses() -> None:
    assert find_unparsable_toml(tracked_files()) == []


def test_every_tracked_text_file_ends_with_a_newline() -> None:
    assert find_missing_final_newline(tracked_files()) == []


def test_no_tracked_text_file_has_trailing_whitespace() -> None:
    assert find_trailing_whitespace(tracked_files()) == []


def test_detector_flags_an_oversized_file(tmp_path: Path) -> None:
    (tmp_path / "big.bin").write_bytes(b"\0" * (MAX_TRACKED_FILE_KB * 1024 + 1))
    (tmp_path / "small.bin").write_bytes(b"\0")
    assert find_oversized_paths(["big.bin", "small.bin"], tmp_path) == ["big.bin"]


def test_detector_flags_unparsable_toml(tmp_path: Path) -> None:
    (tmp_path / "broken.toml").write_text("key = = 1\n", encoding="utf-8")
    (tmp_path / "fine.toml").write_text('key = "value"\n', encoding="utf-8")
    assert find_unparsable_toml(["broken.toml", "fine.toml"], tmp_path) == ["broken.toml"]


def test_detector_flags_a_missing_final_newline(tmp_path: Path) -> None:
    (tmp_path / "no-newline.txt").write_text("text", encoding="utf-8")
    (tmp_path / "newline.txt").write_text("text\n", encoding="utf-8")
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    assert find_missing_final_newline(["no-newline.txt", "newline.txt", "empty.txt"], tmp_path) == [
        "no-newline.txt"
    ]


def test_detector_flags_trailing_whitespace(tmp_path: Path) -> None:
    (tmp_path / "trailing.txt").write_text("text \nmore\n", encoding="utf-8")
    (tmp_path / "clean.txt").write_text("text\nmore\n", encoding="utf-8")
    assert find_trailing_whitespace(["trailing.txt", "clean.txt"], tmp_path) == ["trailing.txt"]


def test_detector_skips_binary_files(tmp_path: Path) -> None:
    """Undecodable bytes are not text-hygiene violations."""
    (tmp_path / "image.bin").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
    assert find_trailing_whitespace(["image.bin"], tmp_path) == []
    assert find_missing_final_newline(["image.bin"], tmp_path) == []
