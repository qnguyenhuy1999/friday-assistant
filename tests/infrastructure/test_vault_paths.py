from __future__ import annotations

import os
from pathlib import Path

import pytest

from friday.application.memory.errors import MemoryAccessDenied
from friday.infrastructure.memory.vault_paths import (
    resolve_vault_path,
    to_vault_relative,
    vault_identity_hash,
)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root.resolve()


@pytest.mark.parametrize(
    "requested",
    ["../secret.md", "/Users/example/secret.md", "nested/../../secret.md"],
)
def test_rejects_path_traversal(requested: str, vault: Path) -> None:
    with pytest.raises(MemoryAccessDenied):
        resolve_vault_path(vault, requested)


def test_rejects_symlink_pointing_outside_the_vault(vault: Path, tmp_path: Path) -> None:
    outside = tmp_path / "secret.md"
    outside.write_text("secret")
    os.symlink(outside, vault / "note.md")

    with pytest.raises(MemoryAccessDenied):
        resolve_vault_path(vault, "note.md")


def test_rejects_symlinked_parent_directory_escaping_the_vault(vault: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, vault / "nested")

    with pytest.raises(MemoryAccessDenied):
        resolve_vault_path(vault, "nested/note.md")


def test_allows_symlink_that_stays_inside_the_vault(vault: Path) -> None:
    target = vault / "target.md"
    target.write_text("note")
    os.symlink(target, vault / "alias.md")

    assert resolve_vault_path(vault, "alias.md") == target


def test_allows_valid_nested_note(vault: Path) -> None:
    note = vault / "nested" / "note.md"
    note.parent.mkdir()
    note.write_text("note")

    assert resolve_vault_path(vault, "nested/note.md") == note


def test_rejects_empty_string(vault: Path) -> None:
    with pytest.raises(MemoryAccessDenied):
        resolve_vault_path(vault, "")


def test_rejects_nul_byte(vault: Path) -> None:
    with pytest.raises(MemoryAccessDenied):
        resolve_vault_path(vault, "note\x00.md")


def test_rejects_a_leading_tilde(vault: Path) -> None:
    with pytest.raises(MemoryAccessDenied):
        resolve_vault_path(vault, "~/note.md")


def test_preserves_case_in_mixed_case_relative_path(vault: Path) -> None:
    note = vault / "Nested" / "MiXeD-Case.md"
    note.parent.mkdir()
    note.write_text("note")

    assert to_vault_relative(vault, note) == "Nested/MiXeD-Case.md"


def test_vault_identity_hash_is_deterministic(vault: Path) -> None:
    assert vault_identity_hash(vault) == vault_identity_hash(vault)
