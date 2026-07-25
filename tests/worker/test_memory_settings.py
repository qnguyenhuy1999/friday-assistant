"""Tests for bounded, opt-in Obsidian memory settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.worker.memory_settings import MemorySettings


def _configure_valid_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("FRIDAY_MEMORY_ENABLED", "true")
    monkeypatch.setenv("FRIDAY_OBSIDIAN_VAULT_ROOT", str(vault))
    monkeypatch.setenv("FRIDAY_OBSIDIAN_MANAGED_ROOT", "Friday")
    monkeypatch.setenv("FRIDAY_MEMORY_INCLUDE_GLOBS", "Notes/**/*.md")
    monkeypatch.setenv("FRIDAY_GRAPHIFY_INDEX_ROOT", str(tmp_path / "graphify"))
    return vault


def test_from_env_expands_tilde_with_lowercase_vault_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    vault = home / "Documents" / "secondbrain"
    vault.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("FRIDAY_MEMORY_ENABLED", "true")
    monkeypatch.setenv("FRIDAY_OBSIDIAN_VAULT_ROOT", "~/Documents/secondbrain")
    monkeypatch.setenv("FRIDAY_MEMORY_INCLUDE_GLOBS", "Notes/**/*.md")
    monkeypatch.setenv("FRIDAY_GRAPHIFY_INDEX_ROOT", str(tmp_path / "index"))

    settings = MemorySettings.from_env()

    assert settings.vault_root == Path.home() / "Documents" / "secondbrain"
    assert settings.vault_root.name == "secondbrain"


def test_enabled_memory_requires_existing_vault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_valid_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("FRIDAY_OBSIDIAN_VAULT_ROOT", str(tmp_path / "missing"))

    with pytest.raises(ValueError, match="must exist"):
        MemorySettings.from_env()


def test_enabled_memory_requires_directory_vault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_valid_environment(monkeypatch, tmp_path)
    file_root = tmp_path / "note.md"
    file_root.write_text("note")
    monkeypatch.setenv("FRIDAY_OBSIDIAN_VAULT_ROOT", str(file_root))

    with pytest.raises(ValueError, match="directory"):
        MemorySettings.from_env()


def test_enabled_memory_requires_explicit_include_globs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_valid_environment(monkeypatch, tmp_path)
    monkeypatch.delenv("FRIDAY_MEMORY_INCLUDE_GLOBS")

    with pytest.raises(ValueError, match="include_globs"):
        MemorySettings.from_env()


@pytest.mark.parametrize("managed_root", ("../outside", "/outside"))
def test_managed_root_cannot_escape_vault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, managed_root: str
) -> None:
    _configure_valid_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("FRIDAY_OBSIDIAN_MANAGED_ROOT", managed_root)

    with pytest.raises(ValueError, match="vault-relative"):
        MemorySettings.from_env()


def test_graphify_index_root_must_be_outside_vault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = _configure_valid_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("FRIDAY_GRAPHIFY_INDEX_ROOT", str(vault / "index"))

    with pytest.raises(ValueError, match="outside"):
        MemorySettings.from_env()


@pytest.mark.parametrize(
    ("environment_name", "value"),
    (
        ("FRIDAY_MEMORY_MAX_FILES", "0"),
        ("FRIDAY_MEMORY_MAX_NOTE_BYTES", "0"),
        ("FRIDAY_MEMORY_MAX_CANDIDATES", "0"),
        ("FRIDAY_MEMORY_MAX_EXCERPTS", "0"),
        ("FRIDAY_MEMORY_MAX_EXCERPT_CHARS", "0"),
        ("FRIDAY_MEMORY_MAX_TOTAL_CONTEXT_CHARS", "0"),
        ("FRIDAY_MEMORY_MAX_GRAPH_DEPTH", "0"),
        ("FRIDAY_MEMORY_MAX_GRAPH_NODES_VISITED", "0"),
        ("FRIDAY_GRAPHIFY_BUILD_TIMEOUT_SECONDS", "0"),
        ("FRIDAY_GRAPHIFY_MAX_STDOUT_BYTES", "0"),
        ("FRIDAY_GRAPHIFY_MAX_STDERR_BYTES", "0"),
        ("FRIDAY_GRAPHIFY_MAX_GRAPH_BYTES", "0"),
        ("FRIDAY_MEMORY_INDEX_MAINTENANCE_SECONDS", "0"),
        ("FRIDAY_MEMORY_INDEX_MAX_FILES_PER_SCAN", "0"),
    ),
)
def test_each_numeric_limit_must_be_positive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, environment_name: str, value: str
) -> None:
    _configure_valid_environment(monkeypatch, tmp_path)
    monkeypatch.setenv(environment_name, value)

    with pytest.raises(ValueError, match="positive"):
        MemorySettings.from_env()


def test_total_context_budget_cannot_exceed_runtime_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_valid_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("FRIDAY_MEMORY_MAX_TOTAL_CONTEXT_CHARS", "60001")

    with pytest.raises(ValueError, match="RuntimeSettings"):
        MemorySettings.from_env()


def test_excerpt_budget_cannot_exceed_total_context_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_valid_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("FRIDAY_MEMORY_MAX_EXCERPTS", "7")

    with pytest.raises(ValueError, match="excerpt budget"):
        MemorySettings.from_env()


def test_graphify_requires_non_empty_executable_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_valid_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("FRIDAY_GRAPHIFY_ENABLED", "true")
    monkeypatch.setenv("FRIDAY_GRAPHIFY_EXECUTABLE", " ")

    with pytest.raises(ValueError, match="graphify_executable"):
        MemorySettings.from_env()


def test_disabled_memory_allows_missing_default_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRIDAY_MEMORY_ENABLED", "false")

    settings = MemorySettings.from_env()

    assert not settings.memory_enabled


def test_default_exclusions_cover_root_level_vault_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_valid_environment(monkeypatch, tmp_path)

    settings = MemorySettings.from_env()

    assert ".git/**" in settings.exclude_globs
    assert ".obsidian/**" in settings.exclude_globs
