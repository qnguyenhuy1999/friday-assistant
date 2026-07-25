"""Tests for LexicalIndexStore: deterministic bounded lexical retrieval."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from friday.application.memory.models import (
    MemoryQuery,
    MemoryVaultPolicy,
)
from friday.infrastructure.memory.lexical_index import LexicalIndexStore

# ── helpers ──────────────────────────────────────────────────────────


def _store(
    tmp_path: Path,
    *,
    maximum: int = 2000,
    bytes_: int = 100_000,
    scanned: int = 5000,
) -> LexicalIndexStore:
    return LexicalIndexStore(
        tmp_path,
        MemoryVaultPolicy(("**/*.md",), (), maximum, bytes_),
        max_files_scanned=scanned,
    )


def _write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _write_bytes(root: Path, path: str, data: bytes) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


# ── signal tests ─────────────────────────────────────────────────────


class TestTitleMatch:
    def test_title_match_returns_title_method(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "---\ntitle: Meeting Notes\n---\nbody")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(titles=("Meeting Notes",)), limit=5)
        assert len(results) == 1
        assert results[0].path == "a.md"
        methods = results[0].methods
        assert methods == ("lexical_title",)

    def test_title_match_case_insensitive(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "---\ntitle: Project Alpha\n---\nbody")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(titles=("project alpha",)), limit=5)
        assert len(results) == 1
        assert "lexical_title" in results[0].methods

    def test_title_no_match_returns_empty(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "---\ntitle: Meeting Notes\n---\nbody")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(titles=("Absent",)), limit=5)
        assert results == ()

    def test_title_fallback_to_filename_stem_when_no_frontmatter(self, tmp_path: Path) -> None:
        _write(tmp_path, "MyNote.md", "just a note")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(titles=("MyNote",)), limit=5)
        assert len(results) == 1
        assert "lexical_title" in results[0].methods


class TestAliasMatch:
    def test_alias_match_returns_alias_method(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "---\ntitle: Main\naliases: [AltName]\n---\nbody")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(titles=("AltName",)), limit=5)
        assert len(results) == 1
        assert "lexical_alias" in results[0].methods

    def test_alias_not_used_when_title_matches(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "---\ntitle: Main\naliases: [AlsoMain]\n---\nbody")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(titles=("Main",)), limit=5)
        assert len(results) == 1
        methods = results[0].methods
        assert "lexical_title" in methods
        assert "lexical_alias" not in methods

    def test_alias_case_insensitive(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "---\ntitle: Main\naliases: [ProjectX]\n---\nbody")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(titles=("projectx",)), limit=5)
        assert len(results) == 1
        assert "lexical_alias" in results[0].methods


class TestTagMatch:
    def test_tag_match_returns_tag_method(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "---\ntitle: N\ntags: [important, urgent]\n---\nbody")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(tags=("important",)), limit=5)
        assert len(results) == 1
        assert "lexical_tag" in results[0].methods

    def test_tag_no_match_returns_nothing(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "---\ntitle: N\ntags: [work]\n---\nbody")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(tags=("personal",)), limit=5)
        assert results == ()


class TestHeadingMatch:
    def test_heading_match_via_term(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "# Introduction\nsome text\n# Details\nmore")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(terms=("Introduction",)), limit=5)
        assert len(results) == 1
        assert "lexical_heading" in results[0].methods

    def test_heading_match_via_phrase(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "# Getting Started\nsome text")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(phrases=("Getting Started",)), limit=5)
        assert len(results) == 1
        assert "lexical_heading" in results[0].methods

    def test_heading_no_match(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "# Status\nno match here")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(terms=("Random",)), limit=5)
        assert results == ()


class TestFilenameMatch:
    def test_filename_match(self, tmp_path: Path) -> None:
        _write(tmp_path, "budget-report.md", "some financial content")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(terms=("budget",)), limit=5)
        assert len(results) >= 1
        assert "lexical_filename" in results[0].methods

    def test_filename_match_reverse(self, tmp_path: Path) -> None:
        _write(tmp_path, "notes-meeting.md", "content")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(terms=("meeting",)), limit=5)
        assert len(results) == 1
        assert "lexical_filename" in results[0].methods

    def test_filename_no_match(self, tmp_path: Path) -> None:
        _write(tmp_path, "random.md", "content")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(terms=("nonexistent",)), limit=5)
        assert results == ()


class TestPhraseMatch:
    def test_exact_phrase_match(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "the quick brown fox jumps over")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(phrases=("quick brown",)), limit=5)
        assert len(results) == 1
        assert "lexical_phrase" in results[0].methods

    def test_phrase_match_case_insensitive(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "Hello World Test")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(phrases=("hello world",)), limit=5)
        assert len(results) == 1
        assert "lexical_phrase" in results[0].methods

    def test_phrase_no_match(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "some random content")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(phrases=("missing phrase",)), limit=5)
        assert results == ()


class TestBodyTermMatch:
    def test_body_term_match(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "the secret password is nowhere")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(terms=("secret",)), limit=5)
        assert len(results) == 1
        assert "lexical_body" in results[0].methods

    def test_body_term_case_insensitive(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "IMPORTANT ANNOUNCEMENT")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(terms=("important",)), limit=5)
        assert len(results) == 1

    def test_body_term_only_when_no_other_match(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "a.md",
            "---\ntitle: Meeting\n---\nmeeting notes content here",
        )
        store = _store(tmp_path)
        results = store.search(MemoryQuery(terms=("notes",), titles=("Meeting",)), limit=5)
        assert len(results) == 1
        methods = results[0].methods
        assert "lexical_title" in methods
        assert "lexical_body" not in methods

    def test_body_term_no_match(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "nothing relevant here")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(terms=("absent",)), limit=5)
        assert results == ()


# ── ranking & tie-breaking ───────────────────────────────────────────


class TestRanking:
    def test_higher_score_ranks_first(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "---\ntitle: Exact Match\n---\nordinary body")
        _write(tmp_path, "b.md", "no match")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(titles=("Exact Match",), terms=("ordinary",)), limit=5)
        assert len(results) >= 1
        assert results[0].path == "a.md"

    def test_tie_break_by_path_ascending(self, tmp_path: Path) -> None:
        _write(tmp_path, "b.md", "---\ntitle: Same\n---\nbody")
        _write(tmp_path, "a.md", "---\ntitle: Same\n---\nbody")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(titles=("Same",)), limit=5)
        assert len(results) == 2
        assert results[0].path == "a.md"
        assert results[1].path == "b.md"

    def test_scores_stable_across_repeated_calls(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "---\ntitle: Stable\n---\ncontent")
        store = _store(tmp_path)
        query = MemoryQuery(titles=("Stable",))
        first = store.search(query, limit=5)
        second = store.search(query, limit=5)
        assert first == second

    def test_identical_scores_across_paths(self, tmp_path: Path) -> None:
        _write(tmp_path, "z.md", "---\ntitle: Alpha\n---\nbody")
        _write(tmp_path, "m.md", "---\ntitle: Beta\n---\nbody")
        query = MemoryQuery(titles=("Alpha", "Beta"))
        store = _store(tmp_path)
        results = store.search(query, limit=5)
        paths = tuple(r.path for r in results)
        assert paths == ("m.md", "z.md")


# ── bounds ───────────────────────────────────────────────────────────


class TestBounds:
    def test_candidate_count_capped(self, tmp_path: Path) -> None:
        for i in range(10):
            _write(tmp_path, f"{i}.md", f"---\ntitle: Note{i}\n---\ncontent")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(titles=("Note",)), limit=3)
        assert len(results) == 3

    def test_limit_zero_returns_empty(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "---\ntitle: Test\n---\nbody")
        store = _store(tmp_path)
        assert store.search(MemoryQuery(titles=("Test",)), limit=0) == ()

    def test_max_files_scanned_honored(self, tmp_path: Path) -> None:
        for i in range(20):
            _write(tmp_path, f"{i}.md", f"title: Note{i}\n---\ncontent")
        store = LexicalIndexStore(
            tmp_path,
            MemoryVaultPolicy(("**/*.md",), (), 2000, 100_000),
            max_files_scanned=5,
        )
        results = store.search(MemoryQuery(titles=("Note",)), limit=200)
        assert len(results) <= 5

    def test_binary_content_skipped(self, tmp_path: Path) -> None:
        _write(tmp_path, "good.md", "---\ntitle: Good\n---\nreadable")
        _write_bytes(tmp_path, "bad.md", b"binary\x00data")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(titles=("Good",)), limit=5)
        assert len(results) == 1

    def test_oversized_note_skipped(self, tmp_path: Path) -> None:
        _write(tmp_path, "big.md", "x" * 500)
        _write(tmp_path, "small.md", "---\ntitle: Small\n---\nok")
        store = LexicalIndexStore(
            tmp_path,
            MemoryVaultPolicy(("**/*.md",), (), 2000, 100),
        )
        results = store.search(MemoryQuery(titles=("Small",)), limit=5)
        assert len(results) == 1


# ── cache behaviour ──────────────────────────────────────────────────


class TestCache:
    def test_second_query_opens_fewer_bodies(self, tmp_path: Path) -> None:
        for i in range(5):
            _write(
                tmp_path,
                f"{i}.md",
                f"---\ntitle: Note{i}\n---\nbody text for content",
            )
        store = _store(tmp_path)
        query = MemoryQuery(terms=("content",))
        store.search(query, limit=5)
        assert store.bodies_opened == 5
        store.search(query, limit=5)
        assert store.bodies_opened == 0

    def test_cache_invalidates_on_size_change(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "---\ntitle: Test\n---\nsmall body")
        store = _store(tmp_path)
        store.search(MemoryQuery(titles=("Test",)), limit=5)
        _write(tmp_path, "a.md", "---\ntitle: Changed\n---\nmuch longer body content here")
        results = store.search(MemoryQuery(titles=("Changed",)), limit=5)
        assert len(results) == 1
        assert "lexical_title" in results[0].methods

    def test_cache_invalidates_on_content_change_identical_mtime(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "---\ntitle: Original\n---\nbody text here")
        store = _store(tmp_path)
        store.search(MemoryQuery(titles=("Original",)), limit=5)
        new_content = "---\ntitle: Modified\n---\nchanged body text"
        _write(tmp_path, "a.md", new_content)
        s = os.stat(tmp_path / "a.md")
        os.utime(tmp_path / "a.md", ns=(s.st_mtime_ns, s.st_mtime_ns))
        results = store.search(MemoryQuery(terms=("changed",)), limit=5)
        assert len(results) == 1
        assert store.bodies_opened == 1

    def test_cache_hit_avoids_rebuilding(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "---\ntitle: Test\n---\nbody")
        store = _store(tmp_path)
        store.search(MemoryQuery(titles=("Test",)), limit=5)
        assert store.bodies_opened == 0
        store.search(MemoryQuery(titles=("Test",)), limit=5)
        assert store.bodies_opened == 0

    def test_cache_invalidates_on_exclusion_path(self, tmp_path: Path) -> None:
        _write(tmp_path, ".claude/private.md", "---\ntitle: Private\n---\nsecret")
        _write(tmp_path, "public.md", "---\ntitle: Public\n---\ninfo")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(titles=("Private",)), limit=5)
        assert results == ()
        results = store.search(MemoryQuery(titles=("Public",)), limit=5)
        assert len(results) == 1

    def test_invalidate_method_clears_entry(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "---\ntitle: Test\n---\nbody content")
        store = _store(tmp_path)
        store.search(MemoryQuery(titles=("Test",)), limit=5)
        store.invalidate("a.md")
        results = store.search(MemoryQuery(terms=("content",)), limit=5)
        assert len(results) == 1
        assert store.bodies_opened == 1


# ── large vault test ─────────────────────────────────────────────────


class TestLargeVault:
    def test_1000_notes_does_not_read_all_bodies(self, tmp_path: Path) -> None:
        for i in range(1000):
            _write(
                tmp_path,
                f"notes/note{i}.md",
                f"---\ntitle: Note{i:04d}\n---\nContent of note {i}.",
            )
        store = _store(tmp_path, bytes_=100_000)
        query = MemoryQuery(titles=("Note0500",))
        results = store.search(query, limit=5)
        assert len(results) >= 1
        assert store.bodies_opened == 0


# ── edge cases ───────────────────────────────────────────────────────


class TestEdgeCases:
    def test_unknown_directory_is_empty(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        results = store.search(MemoryQuery(titles=("Anything",)), limit=5)
        assert results == ()

    def test_binary_only_vault_returns_empty(self, tmp_path: Path) -> None:
        _write_bytes(tmp_path, "binary.md", b"\xff\xfe\x00\x01")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(terms=("test",)), limit=5)
        assert results == ()

    def test_multiple_signals_accumulate(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "project-alpha.md",
            "---\ntitle: Project Alpha\ntags: [important]\n---\n"
            "This project is important for Q3 planning.",
        )
        store = _store(tmp_path)
        results = store.search(
            MemoryQuery(terms=("project",), titles=("Project Alpha",), tags=("important",)),
            limit=5,
        )
        assert len(results) == 1
        candidate = results[0]
        methods = candidate.methods
        assert "lexical_title" in methods
        assert "lexical_tag" in methods
        assert "lexical_filename" in methods
        assert candidate.score > 10.0

    def test_reset_counters_clears_body_count(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "---\ntitle: Test\n---\nbody")
        store = _store(tmp_path)
        store.search(MemoryQuery(titles=("Test",)), limit=5)
        assert store.bodies_opened == 0
        store.search(MemoryQuery(terms=("body",)), limit=5)
        assert store.bodies_opened == 1
        store.reset_counters()
        assert store.bodies_opened == 0


# ── degraded / rejection paths ───────────────────────────────────────


class TestRejectionPaths:
    def test_excluded_paths_not_returned(self, tmp_path: Path) -> None:
        _write(tmp_path, ".obsidian/config.md", "---\ntitle: Config\n---\nsettings")
        _write(tmp_path, "good.md", "---\ntitle: Good\n---\ncontent")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(titles=("Config",)), limit=5)
        assert results == ()

    def test_custom_exclude_globs_honored(self, tmp_path: Path) -> None:
        _write(tmp_path, "drafts/todo.md", "---\ntitle: Todo\n---\ncontent")
        store = LexicalIndexStore(
            tmp_path,
            MemoryVaultPolicy(("**/*.md",), ("drafts/**",), 2000, 100_000),
        )
        results = store.search(MemoryQuery(titles=("Todo",)), limit=5)
        assert results == ()

    def test_no_include_glob_match_excluded(self, tmp_path: Path) -> None:
        _write(tmp_path, "foo.txt", "---\ntitle: Text\n---\ncontent")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(titles=("Text",)), limit=5)
        assert results == ()

    def test_utf8_decode_error_skipped(self, tmp_path: Path) -> None:
        _write_bytes(tmp_path, "bad.md", b"\xff\xfe\x00\x01\xff")
        _write(tmp_path, "good.md", "---\ntitle: Good\n---\ncontent")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(titles=("Good",)), limit=5)
        assert len(results) == 1

    def test_unreadable_file_skipped(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "---\ntitle: Test\n---\nbody")
        store = _store(tmp_path)
        a_path = tmp_path / "a.md"
        a_path.chmod(0o000)
        try:
            results = store.search(MemoryQuery(titles=("Test",)), limit=5)
            assert results == ()
        finally:
            a_path.chmod(0o644)

    def test_empty_vault_returns_empty(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        results = store.search(MemoryQuery(titles=("None",)), limit=5)
        assert results == ()


# ── coverage gap tests ───────────────────────────────────────────────


class TestCoverageBranches:
    def test_valid_cache_oserror_clears_entry(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "---\ntitle: Test\n---\nbody")
        store = _store(tmp_path)
        store.search(MemoryQuery(titles=("Test",)), limit=5)
        os.remove(tmp_path / "a.md")
        cached = store._valid_cache_info("a.md")
        assert cached is None

    def test_alias_reentry_matches_second_alias(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "a.md",
            "---\ntitle: Main\naliases: [SkipThis, MatchThis]\n---\nbody",
        )
        store = _store(tmp_path)
        results = store.search(MemoryQuery(titles=("MatchThis",)), limit=5)
        assert len(results) == 1
        assert "lexical_alias" in results[0].methods

    def test_heading_phrase_match_uses_phrases(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "# Getting Started\nbody")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(phrases=("Getting Started",)), limit=5)
        assert len(results) == 1
        assert "lexical_heading" in results[0].methods

    def test_body_read_handles_oserror(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "---\ntitle: Test\n---\nbody content")
        store = _store(tmp_path)
        a_path = tmp_path / "a.md"
        a_path.chmod(0o000)
        try:
            results = store.search(MemoryQuery(terms=("content",)), limit=5)
            assert results == ()
        finally:
            a_path.chmod(0o644)

    def test_body_read_handles_null_byte(self, tmp_path: Path) -> None:
        _write_bytes(tmp_path, "a.md", b"content\x00more")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(terms=("content",)), limit=5)
        assert results == ()

    def test_content_change_with_same_size_invalidates(
        self,
        tmp_path: Path,
    ) -> None:
        original = "---\ntitle: Original\n---\nbody AAAA"
        modified = "---\ntitle: Modified\n---\nbody BBBB"
        assert len(original) == len(modified)
        _write(tmp_path, "a.md", original)
        store = _store(tmp_path)
        store.search(MemoryQuery(titles=("Original",)), limit=5)
        _write(tmp_path, "a.md", modified)
        s = os.stat(tmp_path / "a.md")
        os.utime(tmp_path / "a.md", ns=(s.st_mtime_ns, s.st_mtime_ns))
        results = store.search(MemoryQuery(terms=("BBBB",)), limit=5)
        assert len(results) == 1
        assert store.bodies_opened == 1

    def test_body_read_none_skips_note_when_file_disappears(
        self,
        tmp_path: Path,
    ) -> None:
        _write(tmp_path, "a.md", "---\ntitle: Test\n---\nbody content")
        store = _store(tmp_path)
        store.search(MemoryQuery(terms=("content",)), limit=5)
        os.remove(tmp_path / "a.md")
        results = store.search(MemoryQuery(terms=("content",)), limit=5)
        assert results == ()

    def test_stale_body_cache_detected(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "---\ntitle: Test\n---\noriginal body")
        store = _store(tmp_path)
        store.search(MemoryQuery(terms=("original",)), limit=5)
        _write(tmp_path, "a.md", "---\ntitle: Test\n---\nmodified body")
        store._body_cache["a.md"] = "---\ntitle: Test\n---\noriginal body"
        store.search(MemoryQuery(terms=("modified",)), limit=5)
        assert store.bodies_opened >= 1

    def test_oserror_in_read_body(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "---\ntitle: Test\n---\nbody content")
        store = _store(tmp_path)
        store.search(MemoryQuery(titles=("Test",)), limit=5)
        a_path = tmp_path / "a.md"
        a_path.chmod(0o000)
        try:
            results = store.search(MemoryQuery(terms=("content",)), limit=5)
            assert results == ()
        finally:
            a_path.chmod(0o644)

    def test_symbolic_link_outside_vault_skipped(self, tmp_path: Path) -> None:
        import tempfile

        outside = Path(tempfile.mkstemp(suffix=".md", prefix="outside_vault_")[1])
        try:
            outside.write_text("outside content")
            sym = tmp_path / "escape.md"
            try:
                sym.symlink_to(outside)
            except OSError:
                return
            store = _store(tmp_path)
            results = store.search(MemoryQuery(terms=("outside",)), limit=5)
            assert results == ()
        finally:
            if outside.exists():
                outside.unlink()

    def test_heading_phrase_multiple_no_match_then_match(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", "# Target Heading\nbody")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(phrases=("Wrong", "Target Heading")), limit=5)
        assert len(results) == 1
        assert "lexical_heading" in results[0].methods

    def test_multiple_methods_dedup(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "project-alpha.md",
            "---\ntitle: Project Alpha\ntags: [important]\n---\n"
            "The alpha project is important for planning.",
        )
        store = _store(tmp_path)
        results = store.search(
            MemoryQuery(
                terms=("project", "alpha", "important"),
                titles=("Project Alpha",),
                tags=("important",),
            ),
            limit=5,
        )
        assert len(results) == 1


class TestPrivacyEligibility:
    def test_lexical_search_excludes_private_note(self, tmp_path: Path) -> None:
        _write(tmp_path, "p.md", "---\nprivate: true\n---\nneedle content")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(terms=("needle",)), limit=5)
        assert results == ()

    def test_lexical_search_excludes_sensitive_note(self, tmp_path: Path) -> None:
        _write(tmp_path, "s.md", "---\nsensitive: true\n---\nneedle content")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(terms=("needle",)), limit=5)
        assert results == ()

    def test_lexical_search_excludes_friday_index_false_note(self, tmp_path: Path) -> None:
        _write(tmp_path, "i.md", "---\nfriday_index: false\n---\nneedle content")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(terms=("needle",)), limit=5)
        assert results == ()

    def test_lexical_search_still_returns_public_note_alongside_private(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path, "p.md", "---\nprivate: true\n---\nneedle content")
        _write(tmp_path, "g.md", "---\ntitle: Good\n---\nneedle content")
        store = _store(tmp_path)
        results = store.search(MemoryQuery(terms=("needle",)), limit=5)
        assert [r.path for r in results] == ["g.md"]


class TestScanCeiling:
    def test_search_honors_directory_entry_scan_ceiling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The raw rglob() enumeration itself must be bounded before
        sorting -- not just the per-file scan loop -- so a pathological
        vault can't force an unbounded path list into memory."""
        import friday.infrastructure.memory.lexical_index as lexical_index_module

        for i in range(10):
            _write(tmp_path, f"{i}.md", f"---\ntitle: Note{i}\n---\ncontent")
        monkeypatch.setattr(lexical_index_module, "_MAX_DIRECTORY_ENTRIES_SCANNED", 3)

        store = _store(tmp_path)
        results = store.search(MemoryQuery(titles=("Note",)), limit=100)
        assert len(results) <= 3
