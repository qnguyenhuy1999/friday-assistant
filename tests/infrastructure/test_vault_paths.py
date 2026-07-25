from __future__ import annotations

import os
from pathlib import Path

import pytest

from friday.application.memory.errors import MemoryAccessDenied
from friday.infrastructure.memory.vault_paths import (
    is_confined_symlink,
    resolve_vault_path,
    resolve_vault_root,
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


# ---------------------------------------------------------------------------
# resolve_vault_root
# ---------------------------------------------------------------------------


def test_resolve_vault_root_missing_root(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    with pytest.raises(MemoryAccessDenied, match="does not exist"):
        resolve_vault_root(missing)


def test_resolve_vault_root_file_instead_of_directory(tmp_path: Path) -> None:
    f = tmp_path / "not-a-dir"
    f.write_text("")
    with pytest.raises(MemoryAccessDenied, match="not a directory"):
        resolve_vault_root(f)


def test_resolve_vault_root_symlinked_root(tmp_path: Path) -> None:
    real = tmp_path / "real_vault"
    real.mkdir()
    link = tmp_path / "link-to-vault"
    os.symlink(real, link)
    result = resolve_vault_root(link)
    assert result == real.resolve()


# ---------------------------------------------------------------------------
# is_confined_symlink  (called directly)
# ---------------------------------------------------------------------------


def test_is_confined_symlink_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    outside = tmp_path / "outside"
    assert not is_confined_symlink(root, outside)


def test_is_confined_symlink_not_yet_existing_final_component(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    candidate = root / "new_file.md"
    assert is_confined_symlink(root, candidate)


def test_is_confined_symlink_not_yet_existing_nested(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    candidate = root / "nested" / "new_file.md"
    assert is_confined_symlink(root, candidate)


def test_is_confined_symlink_symlink_target_escapes(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("secret")
    os.symlink(outside, root / "esc.md")
    assert not is_confined_symlink(root, root / "esc.md")


def test_is_confined_symlink_symlink_stays_inside(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    target = root / "real.md"
    target.write_text("ok")
    os.symlink(target, root / "alias.md")
    assert is_confined_symlink(root, root / "alias.md")


def test_is_confined_symlink_symlinked_parent_escapes(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    os.symlink(outside_dir, root / "sub")
    assert not is_confined_symlink(root, root / "sub" / "note.md")


def test_is_confined_symlink_plain_nested_path(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    nested = root / "a" / "b" / "note.md"
    nested.parent.mkdir(parents=True)
    nested.write_text("hello")
    assert is_confined_symlink(root, nested)


def test_is_confined_symlink_lstat_oserror(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    sub = root / "sub"
    sub.mkdir()
    note = sub / "note.md"
    note.write_text("x")
    sub.chmod(0o000)
    try:
        result = is_confined_symlink(root, note)
    finally:
        sub.chmod(0o755)
    assert not result


def test_resolve_vault_path_second_containment_check(tmp_path: Path) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    sym_parent = tmp_path / "sym"
    os.symlink(real_parent, sym_parent)
    real_root = real_parent / "vault"
    real_root.mkdir()
    note = real_root / "note.md"
    note.write_text("content")
    unresolved_root = sym_parent / "vault"
    with pytest.raises(MemoryAccessDenied, match="escapes the vault"):
        resolve_vault_path(unresolved_root, "note.md")
