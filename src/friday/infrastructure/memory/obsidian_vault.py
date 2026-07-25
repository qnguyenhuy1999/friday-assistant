"""Confined, bounded filesystem implementation of the Obsidian memory store."""

from __future__ import annotations

import hashlib
import itertools
import os
import tempfile
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

from friday.application.memory.errors import (
    MemoryAccessDenied,
    MemoryWriteConflict,
    MemoryWriteDenied,
)
from friday.application.memory.models import (
    MemoryCandidate,
    MemoryExcerpt,
    MemoryQuery,
    MemoryVaultPolicy,
    MemoryWriteCandidate,
    MemoryWriteOperation,
    MemoryWriteResult,
    RetrievalMethod,
)
from friday.infrastructure.memory.file_lock import FileLock
from friday.infrastructure.memory.markdown_parser import parse_markdown
from friday.infrastructure.memory.note_eligibility import is_note_private
from friday.infrastructure.memory.vault_paths import (
    is_confined_symlink,
    resolve_vault_path,
    resolve_vault_root,
    to_vault_relative,
)

_DEFAULT_MANAGED_ROOT = "Friday"
_BUILTIN_EXCLUSIONS = (".obsidian/**", ".trash/**", ".git/**", ".claude/**", "graphify-out/**")
_WRITE_LOCK_TTL_SECONDS = 30.0
_MAX_DIRECTORY_ENTRIES_SCANNED = 200_000
"""Hard ceiling on raw .md paths pulled from rglob() before sorting/capping
by policy -- protects against a pathological vault (or a misconfigured
vault_root pointed at something enormous) from materializing an unbounded
path list in memory before max_files ever gets a chance to apply."""


@dataclass(slots=True)
class ObsidianVaultStore:
    """Read canonical vault notes and perform narrowly-scoped atomic writes."""

    root: Path
    policy: MemoryVaultPolicy
    managed_root: str = _DEFAULT_MANAGED_ROOT
    cap_hit: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.root = resolve_vault_root(self.root)
        if not self.managed_root or self.managed_root.startswith("/") or ".." in self.managed_root:
            raise ValueError("managed_root must be vault-relative")

    def included_paths(self) -> tuple[str, ...]:
        """Return included Markdown paths in stable order; exclusions win."""
        self.cap_hit = False
        included: list[str] = []
        candidates = itertools.islice(self.root.rglob("*.md"), _MAX_DIRECTORY_ENTRIES_SCANNED)
        for file_path in sorted(candidates):
            try:
                if not is_confined_symlink(self.root, file_path):
                    continue
                relative = to_vault_relative(
                    self.root,
                    resolve_vault_path(self.root, file_path.relative_to(self.root).as_posix()),
                )
            except (MemoryAccessDenied, OSError):
                continue
            if not self._path_is_included(relative):
                continue
            try:
                size = file_path.stat().st_size
            except OSError:
                continue
            if size > self.policy.max_note_bytes:
                continue
            text = self._read_text(relative)
            if text is None or self._is_private(text):
                continue
            included.append(relative)
            if len(included) == self.policy.max_files:
                self.cap_hit = any(True for _ in self._remaining_candidates(relative))
                break
        return tuple(included)

    def iter_notes(self) -> tuple[str, ...]:
        return self.included_paths()

    def search_lexical(self, query: MemoryQuery, *, limit: int) -> tuple[MemoryCandidate, ...]:
        if limit <= 0:
            return ()
        terms = tuple(item.casefold() for item in (*query.terms, *query.phrases, *query.titles))
        results: list[MemoryCandidate] = []
        for relative in self.included_paths():
            text = self._read_text(relative)
            if text is None:
                continue
            parsed = parse_markdown(text)
            title = parsed.frontmatter.title or Path(relative).stem
            haystack = f"{title}\n{text}".casefold()
            if terms and not any(term in haystack for term in terms):
                continue
            results.append(MemoryCandidate(relative, title, (RetrievalMethod.LEXICAL_BODY,), 1.0))
            if len(results) == limit:
                break
        return tuple(results)

    def read_excerpt(self, candidate: MemoryCandidate, *, max_chars: int) -> MemoryExcerpt:
        """Authoritative read for every retrieval path (lexical or structural).

        Re-checks eligibility here rather than trusting the caller's
        candidate: a structural (Graphify) candidate never passed through
        ``included_paths()``, so this is the only place that can fail closed
        on a private/sensitive/excluded note before its text leaves the
        vault boundary."""
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        if not self._path_is_included(candidate.path):
            raise MemoryAccessDenied("note is not eligible for retrieval")
        text = self._require_text(candidate.path)
        if self._is_private(text):
            raise MemoryAccessDenied("note is not eligible for retrieval")
        parsed = parse_markdown(text)
        lines = text.splitlines(keepends=True)
        heading = candidate.headings[0] if candidate.headings else None
        start, end = 1, len(lines) or 1
        if heading is not None:
            for body in parsed.heading_bodies:
                if body.heading.text == heading:
                    start, end = body.start_line, body.end_line
                    break
        excerpt = "".join(lines[start - 1 : end])
        clipped = len(excerpt) > max_chars
        if clipped:
            excerpt = excerpt[:max_chars]
        return MemoryExcerpt(
            candidate.path, candidate.title, heading, start, end, excerpt, _hash(excerpt), clipped
        )

    def source_snapshot_hash(self) -> str:
        entries: list[str] = []
        for relative in self.included_paths():
            text = self._require_text(relative)
            entries.append(f"{relative}\0{len(text.encode('utf-8'))}\0{_hash(text)}")
        return _hash("\n".join(entries))

    def write_candidate(self, candidate: MemoryWriteCandidate) -> MemoryWriteResult:
        if not self._is_managed(candidate.path):
            raise MemoryWriteDenied("write target is outside the managed root")
        path = resolve_vault_path(self.root, candidate.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = self._note_lock(path)
        if not lock.acquire():
            raise MemoryWriteConflict("another write to this note is in progress")
        try:
            if candidate.operation is MemoryWriteOperation.CREATE_NOTE:
                content = _render_created_note(candidate)
                self._atomic_create(path, content)
                return MemoryWriteResult(
                    candidate.path,
                    candidate.operation,
                    _hash(content),
                    True,
                    len(content.encode()),
                )
            return self._append(path, candidate)
        finally:
            lock.release()

    def _note_lock(self, path: Path) -> FileLock:
        """Serialize Friday's own concurrent writers for one note. A human
        editor bypasses this (it has no reason to know about it), so
        _append still re-verifies the content hash immediately before
        publication rather than relying on the lock alone."""
        return FileLock(
            path.parent / f".friday-lock-{path.name}", _WRITE_LOCK_TTL_SECONDS, "memory-write"
        )

    def _append(self, path: Path, candidate: MemoryWriteCandidate) -> MemoryWriteResult:
        if not path.exists():
            raise MemoryWriteDenied("managed note does not exist")
        before = self._require_text(candidate.path)
        if _hash(before) != candidate.expected_content_hash:
            raise MemoryWriteConflict("managed note changed before append")
        if not parse_markdown(before).frontmatter.friday_managed:
            raise MemoryWriteDenied("append target is not friday-managed")
        newline = "\r\n" if "\r\n" in before else "\n"
        content = (
            before
            + ("" if before.endswith(("\n", "\r")) else newline)
            + candidate.payload.replace("\n", newline)
        )
        # Re-verify immediately before publication: the lock above only
        # serializes Friday's own writers, so this closes as much of the
        # remaining external-mutation window as possible.
        current = self._require_text(candidate.path)
        if _hash(current) != candidate.expected_content_hash:
            raise MemoryWriteConflict("managed note changed before append")
        self._atomic_write(path, content)
        return MemoryWriteResult(
            candidate.path, candidate.operation, _hash(content), False, len(content.encode())
        )

    def _atomic_create(self, path: Path, content: str) -> None:
        """Publish via a hard link, which atomically fails if *path* already
        exists -- unlike os.replace(), there is no check-then-write window
        for a concurrent create to race through."""
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".friday-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                raise MemoryWriteDenied("refusing to overwrite an existing note") from None
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _read_text(self, relative: str) -> str | None:
        try:
            if not is_confined_symlink(self.root, self.root / relative):
                return None
            path = resolve_vault_path(self.root, relative)
            data = path.read_bytes()
        except (MemoryAccessDenied, OSError):
            return None
        if len(data) > self.policy.max_note_bytes or b"\0" in data:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def _require_text(self, relative: str) -> str:
        text = self._read_text(relative)
        if text is None:
            raise MemoryAccessDenied("note is unreadable, binary, or exceeds the configured limit")
        return text

    def _path_is_included(self, relative: str) -> bool:
        if any(
            _glob_matches(relative, pattern)
            for pattern in (*_BUILTIN_EXCLUSIONS, *self.policy.exclude_globs)
        ):
            return False
        return any(_glob_matches(relative, pattern) for pattern in self.policy.include_globs)

    def _is_private(self, text: str) -> bool:
        return is_note_private(text)

    def _is_managed(self, relative: str) -> bool:
        return relative.startswith(f"{self.managed_root.rstrip('/')}/") and relative.endswith(".md")

    def _remaining_candidates(self, after: str) -> tuple[Path, ...]:
        candidates = itertools.islice(self.root.rglob("*.md"), _MAX_DIRECTORY_ENTRIES_SCANNED)
        return tuple(path for path in candidates if path.relative_to(self.root).as_posix() > after)

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".friday-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def _glob_matches(path: str, pattern: str) -> bool:
    return fnmatchcase(path, pattern) or (
        pattern.startswith("**/") and fnmatchcase(path, pattern[3:])
    )


def _render_created_note(candidate: MemoryWriteCandidate) -> str:
    frontmatter = "\n".join(f"{key}: {value}" for key, value in candidate.frontmatter)
    return f"---\n{frontmatter}\n---\n{candidate.payload}"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
