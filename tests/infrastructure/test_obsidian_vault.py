from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest

from friday.application.memory.errors import (
    MemoryAccessDenied,
    MemoryWriteConflict,
    MemoryWriteDenied,
)
from friday.application.memory.models import (
    MemoryCandidate,
    MemoryQuery,
    MemoryVaultPolicy,
    MemoryWriteCandidate,
    MemoryWriteOperation,
    RetrievalMethod,
)
from friday.infrastructure.memory import obsidian_vault
from friday.infrastructure.memory.obsidian_vault import ObsidianVaultStore


def _store(tmp_path: Path, *, maximum: int = 20, bytes_: int = 10000) -> ObsidianVaultStore:
    return ObsidianVaultStore(tmp_path, MemoryVaultPolicy(("**/*.md",), (), maximum, bytes_))


def _write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _candidate(path: str, **changes: Any) -> MemoryWriteCandidate:
    values: dict[str, Any] = dict(
        operation=MemoryWriteOperation.CREATE_NOTE,
        path=path,
        expected_content_hash=None,
        payload="hello",
        frontmatter=(("friday_managed", "true"),),
        memory_category="explicit_decision",
    )
    values.update(changes)
    return MemoryWriteCandidate(**values)


def test_included_paths_orders_and_applies_exclusions_and_frontmatter(tmp_path: Path) -> None:
    _write(tmp_path, "z.md", "z")
    _write(tmp_path, "a.md", "a")
    _write(tmp_path, ".obsidian/x.md", "x")
    _write(tmp_path, "p.md", "---\nprivate: true\n---\nx")
    _write(tmp_path, "i.md", "---\nfriday_index: false\n---\nx")
    assert _store(tmp_path).included_paths() == ("a.md", "z.md")


def test_included_paths_omits_symlink_to_outside_vault(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    _write(tmp_path, "real.md", "safe")
    os.symlink(outside, tmp_path / "escape.md")

    assert _store(tmp_path).included_paths() == ("real.md",)


def test_included_paths_omits_escaping_symlinked_parent(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside"
    outside.mkdir()
    _write(outside, "secret.md", "secret")
    _write(tmp_path, "real.md", "safe")
    os.symlink(outside, tmp_path / "escape")

    assert _store(tmp_path).included_paths() == ("real.md",)


def test_included_paths_allows_symlink_to_note_inside_vault(tmp_path: Path) -> None:
    _write(tmp_path, "real.md", "safe")
    os.symlink(tmp_path / "real.md", tmp_path / "alias.md")

    assert _store(tmp_path).included_paths() == ("real.md", "real.md")


def test_read_excerpt_rejects_symlink_to_outside_vault(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    os.symlink(outside, tmp_path / "escape.md")
    candidate = MemoryCandidate("escape.md", "escape", (RetrievalMethod.LEXICAL_BODY,), 1)

    with pytest.raises(MemoryAccessDenied):
        _store(tmp_path).read_excerpt(candidate, max_chars=20)


def test_included_paths_skips_note_removed_before_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "gone.md", "gone")
    _write(tmp_path, "real.md", "safe")
    original_stat = Path.stat

    def remove_before_stat(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        if path == tmp_path / "gone.md":
            path.unlink()
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", remove_before_stat)

    assert _store(tmp_path).included_paths() == ("real.md",)


def test_included_paths_skips_path_rejected_by_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "note.md", "safe")

    def deny_path(root: Path, requested: str) -> Path:
        raise MemoryAccessDenied("path escapes the vault")

    monkeypatch.setattr(obsidian_vault, "resolve_vault_path", deny_path)

    assert _store(tmp_path).included_paths() == ()


def test_included_paths_skips_oversize_note(tmp_path: Path) -> None:
    _write(tmp_path, "large.md", "large")
    _write(tmp_path, "small.md", "ok")

    assert _store(tmp_path, bytes_=4).included_paths() == ("small.md",)


def test_cap_and_oversize_and_binary_are_bounded(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", "a")
    _write(tmp_path, "b.md", "b")
    store = _store(tmp_path, maximum=1, bytes_=4)
    assert store.included_paths() == ("a.md",)
    assert store.cap_hit
    (tmp_path / "binary.md").write_bytes(b"a\0b")
    with pytest.raises(MemoryAccessDenied):
        store.read_excerpt(
            MemoryCandidate("binary.md", "b", (RetrievalMethod.LEXICAL_BODY,), 1), max_chars=3
        )


def test_excerpt_is_authoritative_heading_aware_and_truncated(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", "intro\n# One\nabc😀def\n# Two\nend\n")
    candidate = MemoryCandidate("a.md", "a", (RetrievalMethod.LEXICAL_BODY,), 1, ("One",))
    excerpt = _store(tmp_path).read_excerpt(candidate, max_chars=5)
    assert (excerpt.start_line, excerpt.end_line, excerpt.truncated) == (2, 3, True)
    assert excerpt.text == "# One"
    assert excerpt.content_hash == hashlib.sha256(excerpt.text.encode()).hexdigest()


def test_excerpt_without_heading_uses_the_whole_note(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", "body")
    candidate = MemoryCandidate("a.md", "a", (RetrievalMethod.LEXICAL_BODY,), 1)

    excerpt = _store(tmp_path).read_excerpt(candidate, max_chars=20)

    assert (excerpt.start_line, excerpt.end_line, excerpt.text) == (1, 1, "body")


def test_snapshot_is_stable_and_changes_with_content(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", "one")
    store = _store(tmp_path)
    before = store.source_snapshot_hash()
    assert before == store.source_snapshot_hash()
    _write(tmp_path, "a.md", "two")
    assert store.source_snapshot_hash() != before


def test_write_create_and_append_are_atomic_and_preserve_crlf(tmp_path: Path) -> None:
    store = _store(tmp_path)
    created = _candidate("Friday/Inbox/new.md")
    result = store.write_candidate(created)
    assert result.created and (tmp_path / "Friday/Inbox/new.md").exists()
    with pytest.raises(MemoryWriteDenied):
        store.write_candidate(created)
    existing = "---\r\nfriday_managed: true\r\n---\r\nold\r\n"
    _write(tmp_path, "Friday/Inbox/a.md", existing)
    appended = _candidate(
        "Friday/Inbox/a.md",
        operation=MemoryWriteOperation.APPEND_MANAGED_NOTE,
        expected_content_hash=hashlib.sha256(existing.encode()).hexdigest(),
        frontmatter=(),
        payload="new",
    )
    store.write_candidate(appended)
    assert b"\r\nnew" in (tmp_path / "Friday/Inbox/a.md").read_bytes()
    assert not tuple((tmp_path / "Friday/Inbox").glob(".friday-*"))


def test_write_denials_and_conflict_leave_existing_bytes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(MemoryWriteDenied):
        store.write_candidate(_candidate("Elsewhere/a.md"))
    original = "---\nfriday_managed: false\n---\nold"
    _write(tmp_path, "Friday/Inbox/a.md", original)
    append = _candidate(
        "Friday/Inbox/a.md",
        operation=MemoryWriteOperation.APPEND_MANAGED_NOTE,
        expected_content_hash="0" * 64,
        frontmatter=(),
    )
    with pytest.raises(MemoryWriteConflict):
        store.write_candidate(append)
    assert (tmp_path / "Friday/Inbox/a.md").read_text() == original
    append = _candidate(
        "Friday/Inbox/a.md",
        operation=MemoryWriteOperation.APPEND_MANAGED_NOTE,
        expected_content_hash=hashlib.sha256(original.encode()).hexdigest(),
        frontmatter=(),
    )
    with pytest.raises(MemoryWriteDenied):
        store.write_candidate(append)


def test_lexical_search_limits_and_empty_query_paths(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", "---\ntitle: Alpha\n---\nneedle")
    _write(tmp_path, "b.md", "other")
    store = _store(tmp_path)
    assert store.search_lexical(MemoryQuery(terms=("needle",)), limit=1)[0].path == "a.md"
    assert store.search_lexical(MemoryQuery(terms=("absent",)), limit=2) == ()
    assert store.search_lexical(MemoryQuery(terms=("needle",)), limit=0) == ()
    assert store.iter_notes() == ("a.md", "b.md")


def test_lexical_search_skips_note_that_becomes_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "a.md", "needle")
    store = _store(tmp_path)
    original_read_text = ObsidianVaultStore._read_text
    calls = 0

    def disappear_after_enumeration(instance: ObsidianVaultStore, relative: str) -> str | None:
        nonlocal calls
        calls += 1
        return original_read_text(instance, relative) if calls == 1 else None

    monkeypatch.setattr(ObsidianVaultStore, "_read_text", disappear_after_enumeration)

    assert store.search_lexical(MemoryQuery(terms=("needle",)), limit=1) == ()


def test_direct_error_and_predicate_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ObsidianVaultStore(tmp_path, MemoryVaultPolicy(("**/*.md",), (), 1, 1), "/Friday")
    _write(tmp_path, "a.md", "x")
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.read_excerpt(
            MemoryCandidate("a.md", "a", (RetrievalMethod.LEXICAL_BODY,), 1), max_chars=0
        )
    assert store._path_is_included("a.md")
    assert not store._path_is_included(".trash/a.md")
    assert store._is_private("---\nsensitive: true\n---\nx")
    assert not store._is_private("plain")
    assert store._is_managed("Friday/Inbox/a.md")
    assert not store._is_managed("Friday/Inbox/a.txt")
    with pytest.raises(MemoryWriteDenied):
        store.write_candidate(
            _candidate(
                "Friday/Inbox/missing.md",
                operation=MemoryWriteOperation.APPEND_MANAGED_NOTE,
                expected_content_hash="a" * 64,
                frontmatter=(),
            )
        )
    (tmp_path / "bad.md").write_bytes(b"\xff")
    assert store._read_text("bad.md") is None
    assert store._read_text("missing.md") is None


def test_remaining_search_heading_miss_and_atomic_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "a.md", "# Actual\nbody")
    store = _store(tmp_path)
    assert len(store.search_lexical(MemoryQuery(terms=("body",)), limit=2)) == 1
    missing_heading = MemoryCandidate("a.md", "a", (RetrievalMethod.LEXICAL_BODY,), 1, ("Missing",))
    assert store.read_excerpt(missing_heading, max_chars=100).truncated is False

    def fail_replace(source: str, target: str) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        store._atomic_write(tmp_path / "atomic.md", "content")
    assert not tuple(tmp_path.glob(".friday-*"))
