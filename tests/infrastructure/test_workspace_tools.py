"""Workspace confinement and file-tool tests: traversal/symlink escapes,
deterministic listing, UTF-8 reads with truncation, atomic writes with
checksums and artifact candidates."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from friday.application.errors import ToolInputInvalid, WorkspaceAccessDenied
from friday.infrastructure.tools.workspace_files import WorkspaceFiles, WorkspaceFileSettings
from friday.infrastructure.tools.workspace_paths import (
    resolve_workspace_path,
    resolve_workspace_root,
    to_workspace_relative,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "docs").mkdir()
    (root / "docs" / "readme.md").write_text("hello docs")
    (root / "notes.txt").write_text("hello notes")
    (tmp_path / "outside-secret.txt").write_text("secret")
    return root


def files(workspace: Path, **overrides: object) -> WorkspaceFiles:
    fields: dict[str, object] = {
        "root": resolve_workspace_root(workspace),
        "max_file_bytes": 10_000,
        "max_list_entries": 100,
    }
    fields.update(overrides)
    return WorkspaceFiles(WorkspaceFileSettings(**fields))  # type: ignore[arg-type]


# --- confinement ----------------------------------------------------------


def test_valid_nested_path_resolves(workspace: Path) -> None:
    root = resolve_workspace_root(workspace)
    resolved = resolve_workspace_path(root, "docs/readme.md")
    assert resolved == root / "docs" / "readme.md"
    assert to_workspace_relative(root, resolved) == "docs/readme.md"


@pytest.mark.parametrize(
    "requested",
    [
        "../outside-secret.txt",
        "docs/../../outside-secret.txt",
        "/etc/passwd",
        "~/secrets",
        "..",
        "docs/../..",
        "",
        "docs/\x00evil",
    ],
)
def test_escaping_paths_are_denied(workspace: Path, requested: str) -> None:
    root = resolve_workspace_root(workspace)
    with pytest.raises(WorkspaceAccessDenied):
        resolve_workspace_path(root, requested)


def test_symlink_to_outside_is_denied(workspace: Path, tmp_path: Path) -> None:
    root = resolve_workspace_root(workspace)
    (workspace / "sneaky").symlink_to(tmp_path / "outside-secret.txt")
    with pytest.raises(WorkspaceAccessDenied):
        resolve_workspace_path(root, "sneaky")


def test_symlinked_parent_directory_is_denied(workspace: Path, tmp_path: Path) -> None:
    root = resolve_workspace_root(workspace)
    (workspace / "linkdir").symlink_to(tmp_path)
    with pytest.raises(WorkspaceAccessDenied):
        resolve_workspace_path(root, "linkdir/outside-secret.txt")


def test_internal_symlink_is_allowed(workspace: Path) -> None:
    root = resolve_workspace_root(workspace)
    (workspace / "alias.txt").symlink_to(workspace / "notes.txt")
    resolved = resolve_workspace_path(root, "alias.txt")
    assert resolved == root / "notes.txt"


def test_missing_workspace_root_is_denied(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceAccessDenied):
        resolve_workspace_root(tmp_path / "does-not-exist")


def test_file_as_workspace_root_is_denied(workspace: Path) -> None:
    with pytest.raises(WorkspaceAccessDenied):
        resolve_workspace_root(workspace / "notes.txt")


# --- workspace.list -------------------------------------------------------


def test_list_is_deterministic_and_sorted(workspace: Path) -> None:
    result = files(workspace).list_entries({})
    assert result.status == "succeeded"
    assert isinstance(result.output, dict)
    entries = result.output["entries"]
    assert isinstance(entries, list)
    paths = [str(entry["path"]) for entry in entries if isinstance(entry, dict)]
    assert paths == sorted(paths)
    assert "docs" in paths and "notes.txt" in paths


def test_list_shallow_recursion_includes_one_level(workspace: Path) -> None:
    result = files(workspace).list_entries({"recursive": True})
    assert isinstance(result.output, dict)
    entries = result.output["entries"]
    assert isinstance(entries, list)
    paths = [entry["path"] for entry in entries if isinstance(entry, dict)]
    assert "docs/readme.md" in paths


def test_list_caps_entry_count_with_truncated_flag(workspace: Path) -> None:
    for index in range(10):
        (workspace / f"file-{index:02}.txt").write_text("x")
    result = files(workspace, max_list_entries=3).list_entries({})
    assert isinstance(result.output, dict)
    entries = result.output["entries"]
    assert isinstance(entries, list)
    assert len(entries) == 3
    assert result.output["truncated"] is True


def test_list_missing_directory_fails_structurally(workspace: Path) -> None:
    result = files(workspace).list_entries({"path": "nope"})
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "tool_execution_failed"


def test_list_rejects_unknown_input_key(workspace: Path) -> None:
    with pytest.raises(ToolInputInvalid):
        files(workspace).list_entries({"paths": "."})


# --- workspace.read_text --------------------------------------------------


def test_read_returns_content_and_size(workspace: Path) -> None:
    result = files(workspace).read_text({"path": "notes.txt"})
    assert result.status == "succeeded"
    assert result.output == {"content": "hello notes", "size": 11, "truncated": False}


def test_read_missing_file_fails_structurally(workspace: Path) -> None:
    result = files(workspace).read_text({"path": "missing.txt"})
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "tool_execution_failed"


def test_read_binary_file_is_rejected(workspace: Path) -> None:
    (workspace / "blob.bin").write_bytes(b"\x00\x01\x02")
    with pytest.raises(ToolInputInvalid):
        files(workspace).read_text({"path": "blob.bin"})


def test_read_non_utf8_file_is_rejected(workspace: Path) -> None:
    (workspace / "latin.txt").write_bytes("café".encode("latin-1"))
    with pytest.raises(ToolInputInvalid):
        files(workspace).read_text({"path": "latin.txt"})


def test_read_oversized_file_truncates_with_marker(workspace: Path) -> None:
    (workspace / "big.txt").write_text("a" * 500)
    result = files(workspace, max_file_bytes=100).read_text({"path": "big.txt"})
    assert isinstance(result.output, dict)
    assert result.output["truncated"] is True
    assert result.output["size"] == 500
    content = result.output["content"]
    assert isinstance(content, str)
    assert content.endswith("…[truncated: file exceeds read limit]")


def test_read_truncation_never_splits_a_multibyte_character(workspace: Path) -> None:
    (workspace / "emoji.txt").write_text("🎉" * 100)  # 4 bytes each
    result = files(workspace, max_file_bytes=10).read_text({"path": "emoji.txt"})
    assert isinstance(result.output, dict)
    content = result.output["content"]
    assert isinstance(content, str)
    assert content.startswith("🎉🎉")  # 10 bytes -> 2 whole emoji, partial dropped


def test_read_directory_is_rejected(workspace: Path) -> None:
    with pytest.raises(ToolInputInvalid):
        files(workspace).read_text({"path": "docs"})


def test_read_symlink_escape_is_denied(workspace: Path, tmp_path: Path) -> None:
    (workspace / "sneaky.txt").symlink_to(tmp_path / "outside-secret.txt")
    with pytest.raises(WorkspaceAccessDenied):
        files(workspace).read_text({"path": "sneaky.txt"})


# --- workspace.write_text -------------------------------------------------


def test_write_creates_file_with_checksum_and_artifact(workspace: Path) -> None:
    result = files(workspace).write_text(
        {"path": "out/new.md", "content": "# hi", "create_parents": True}
    )
    assert result.status == "succeeded"
    assert (workspace / "out" / "new.md").read_text() == "# hi"
    expected_checksum = hashlib.sha256(b"# hi").hexdigest()
    assert isinstance(result.output, dict)
    assert result.output["checksum"] == expected_checksum
    assert result.output["created"] is True
    assert len(result.artifacts) == 1
    candidate = result.artifacts[0]
    assert candidate.location == "out/new.md"
    assert candidate.checksum == expected_checksum
    assert candidate.media_type == "text/markdown"
    assert candidate.size == 4


def test_write_without_overwrite_rejects_existing(workspace: Path) -> None:
    with pytest.raises(ToolInputInvalid):
        files(workspace).write_text({"path": "notes.txt", "content": "clobber"})
    assert (workspace / "notes.txt").read_text() == "hello notes"


def test_write_with_overwrite_replaces(workspace: Path) -> None:
    result = files(workspace).write_text({"path": "notes.txt", "content": "v2", "overwrite": True})
    assert result.status == "succeeded"
    assert (workspace / "notes.txt").read_text() == "v2"
    assert isinstance(result.output, dict)
    assert result.output["created"] is False


def test_write_missing_parent_without_flag_is_rejected(workspace: Path) -> None:
    with pytest.raises(ToolInputInvalid):
        files(workspace).write_text({"path": "no-dir/x.txt", "content": "x"})


def test_write_oversized_content_is_rejected(workspace: Path) -> None:
    with pytest.raises(ToolInputInvalid):
        files(workspace, max_file_bytes=10).write_text({"path": "big.txt", "content": "x" * 11})


def test_write_outside_workspace_is_denied(workspace: Path) -> None:
    with pytest.raises(WorkspaceAccessDenied):
        files(workspace).write_text({"path": "../evil.txt", "content": "x"})


def test_write_to_directory_is_rejected(workspace: Path) -> None:
    with pytest.raises(ToolInputInvalid):
        files(workspace).write_text({"path": "docs", "content": "x"})


def test_write_leaves_no_temp_file_behind(workspace: Path) -> None:
    files(workspace).write_text({"path": "clean.txt", "content": "x"})
    leftovers = [p.name for p in workspace.iterdir() if p.name.startswith(".friday-write-")]
    assert leftovers == []
