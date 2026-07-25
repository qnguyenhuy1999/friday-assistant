"""Deterministic bounded lexical retrieval over authoritative Markdown.

No embeddings, no vector search, no LLM ranking -- deterministic code only.

Scoring formula (per-candidate):
    score = TITLE_MATCH_WEIGHT   * has_title_match
          + ALIAS_MATCH_WEIGHT   * has_alias_match
          + TAG_MATCH_WEIGHT     * has_tag_match
          + HEADING_MATCH_WEIGHT * has_heading_match
          + FILENAME_MATCH_WEIGHT* has_filename_match
          + PHRASE_MATCH_WEIGHT  * has_phrase_match
          + BODY_TERM_MATCH_WEIGHT*has_body_term_match

Each boolean predicate is 0 or 1.  The first matching signal from the
title/alias pair short-circuits (title wins).  All other signals are
independent and additive.

Tie-breaking: vault-relative path (ascending POSIX compare).

Weight constants (documented here, the one source of truth):
    TITLE_MATCH_WEIGHT    = 10.0
    ALIAS_MATCH_WEIGHT    = 8.0
    TAG_MATCH_WEIGHT      = 7.0
    HEADING_MATCH_WEIGHT  = 6.0
    FILENAME_MATCH_WEIGHT = 5.0
    PHRASE_MATCH_WEIGHT   = 4.0
    BODY_TERM_MATCH_WEIGHT= 1.0
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from friday.application.memory.errors import MemoryAccessDenied
from friday.application.memory.models import (
    MemoryCandidate,
    MemoryQuery,
    MemoryVaultPolicy,
    RetrievalMethod,
)
from friday.infrastructure.memory.markdown_parser import parse_markdown
from friday.infrastructure.memory.vault_paths import (
    resolve_vault_path,
    resolve_vault_root,
    to_vault_relative,
)

_TITLE_MATCH_WEIGHT = 10.0
_ALIAS_MATCH_WEIGHT = 8.0
_TAG_MATCH_WEIGHT = 7.0
_HEADING_MATCH_WEIGHT = 6.0
_FILENAME_MATCH_WEIGHT = 5.0
_PHRASE_MATCH_WEIGHT = 4.0
_BODY_TERM_MATCH_WEIGHT = 1.0

_DEFAULT_MAX_FILES_SCANNED = 5000
_BUILTIN_EXCLUSIONS = (
    ".obsidian/**",
    ".trash/**",
    ".git/**",
    ".claude/**",
    "graphify-out/**",
    "Attachments/**",
    "Templates/**",
)


@dataclass(frozen=True, slots=True)
class _CachedNoteInfo:
    size: int
    mtime_ns: int
    content_hash: str
    title: str
    aliases: tuple[str, ...]
    tags: tuple[str, ...]
    headings: tuple[str, ...]


class LexicalIndexStore:
    """Local lexical retriever over vault Markdown files.

    Args:
        root: Vault root directory.
        policy: Vault policy controlling includes, excludes, and size caps.
        max_files_scanned: Hard cap on the number of ``*.md`` files inspected
            per query (prevents unbounded directory walks).
    """

    def __init__(
        self,
        root: Path,
        policy: MemoryVaultPolicy,
        *,
        max_files_scanned: int = _DEFAULT_MAX_FILES_SCANNED,
    ) -> None:
        self._root = resolve_vault_root(root)
        self._policy = policy
        self._max_files_scanned = max_files_scanned
        self._cache: dict[str, _CachedNoteInfo] = {}
        self._body_cache: dict[str, str] = {}
        self._bodies_opened: int = 0

    @property
    def bodies_opened(self) -> int:
        return self._bodies_opened

    def reset_counters(self) -> None:
        self._bodies_opened = 0

    def invalidate(self, relative: str) -> None:
        self._cache.pop(relative, None)
        self._body_cache.pop(relative, None)

    # ── cache helpers ─────────────────────────────────────────────────

    def _valid_cache_info(self, relative: str) -> _CachedNoteInfo | None:
        cached = self._cache.get(relative)
        if cached is None:
            return None
        try:
            path = resolve_vault_path(self._root, relative)
            s = path.stat()
        except OSError:
            self._cache.pop(relative, None)
            self._body_cache.pop(relative, None)
            return None
        if s.st_size != cached.size or s.st_mtime_ns != cached.mtime_ns:
            self._cache.pop(relative, None)
            self._body_cache.pop(relative, None)
            return None
        return cached

    def _build_metadata(
        self, relative: str, data: bytes, text: str, size: int, mtime_ns: int
    ) -> _CachedNoteInfo | None:
        parsed = parse_markdown(text)
        title = parsed.frontmatter.title or Path(relative).stem
        info = _CachedNoteInfo(
            size=size,
            mtime_ns=mtime_ns,
            content_hash=hashlib.sha256(data).hexdigest(),
            title=title,
            aliases=parsed.frontmatter.aliases,
            tags=parsed.frontmatter.tags,
            headings=tuple(h.text for h in parsed.headings),
        )
        self._cache[relative] = info
        return info

    def _load_metadata(self, relative: str) -> _CachedNoteInfo | None:
        cached = self._valid_cache_info(relative)
        if cached is not None:
            return cached
        try:
            path = resolve_vault_path(self._root, relative)
            s = path.stat()
            data = path.read_bytes()
        except OSError:
            return None
        if len(data) > self._policy.max_note_bytes or b"\0" in data:
            return None
        text = data.decode("utf-8")
        return self._build_metadata(relative, data, text, s.st_size, s.st_mtime_ns)

    # ── I/O (body reads, counted in bodies_opened) ────────────────────

    def _read_body(self, relative: str) -> str | None:
        body = self._body_cache.get(relative)
        if body is not None:
            cached = self._valid_cache_info(relative)
            if cached is not None:
                return body
        try:
            path = resolve_vault_path(self._root, relative)
            s = path.stat()
            data = path.read_bytes()
        except OSError:
            return None
        if len(data) > self._policy.max_note_bytes or b"\0" in data:
            return None
        text = data.decode("utf-8")
        cached = self._cache.get(relative)
        if cached is not None:
            actual_hash = hashlib.sha256(data).hexdigest()
            if (
                s.st_size != cached.size
                or s.st_mtime_ns != cached.mtime_ns
                or actual_hash != cached.content_hash
            ):
                self._cache.pop(relative, None)
                self._body_cache.pop(relative, None)
        self._build_metadata(relative, data, text, s.st_size, s.st_mtime_ns)
        self._body_cache[relative] = text
        self._bodies_opened += 1
        return text

    # ── path inclusion ────────────────────────────────────────────────

    def _path_is_included(self, relative: str) -> bool:
        if any(
            _glob_matches(relative, pattern)
            for pattern in (*_BUILTIN_EXCLUSIONS, *self._policy.exclude_globs)
        ):
            return False
        return any(_glob_matches(relative, pattern) for pattern in self._policy.include_globs)

    # ── public API ────────────────────────────────────────────────────

    def search(self, query: MemoryQuery, *, limit: int) -> tuple[MemoryCandidate, ...]:
        """Run a lexical query against the vault.

        Returns ``MemoryCandidate`` tuples sorted by score descending, then
        vault-relative path ascending for deterministic tie-breaking.
        """
        if limit <= 0:
            return ()
        self._bodies_opened = 0

        terms_cf = tuple(t.casefold() for t in query.terms)
        phrases_cf = tuple(p.casefold() for p in query.phrases)
        titles_cf = tuple(t.casefold() for t in query.titles)
        tags_cf = tuple(t.casefold() for t in query.tags)

        scored: list[tuple[float, str, MemoryCandidate]] = []
        scanned = 0

        for file_path in sorted(self._root.rglob("*.md")):
            if scanned >= self._max_files_scanned:
                break
            scanned += 1
            try:
                relative = to_vault_relative(
                    self._root,
                    resolve_vault_path(self._root, file_path.relative_to(self._root).as_posix()),
                )
            except (OSError, ValueError, MemoryAccessDenied):
                continue

            if not self._path_is_included(relative):
                continue

            info = self._load_metadata(relative)
            if info is None:
                continue

            score = 0.0
            methods: list[RetrievalMethod] = []

            if titles_cf:
                title_cf = info.title.casefold()
                for qt in titles_cf:
                    if qt in title_cf:
                        score += _TITLE_MATCH_WEIGHT
                        methods.append(RetrievalMethod.LEXICAL_TITLE)
                        break

            if titles_cf and not any(m == RetrievalMethod.LEXICAL_TITLE for m in methods):
                for alias in info.aliases:
                    if alias.casefold() in titles_cf:
                        score += _ALIAS_MATCH_WEIGHT
                        methods.append(RetrievalMethod.LEXICAL_ALIAS)
                        break

            if tags_cf and info.tags:
                note_tags_cf = frozenset(t.casefold() for t in info.tags)
                if any(t in note_tags_cf for t in tags_cf):
                    score += _TAG_MATCH_WEIGHT
                    methods.append(RetrievalMethod.LEXICAL_TAG)

            if terms_cf:
                filename_stem = Path(relative).stem.casefold()
                for qt in terms_cf:
                    if qt in filename_stem:
                        score += _FILENAME_MATCH_WEIGHT
                        methods.append(RetrievalMethod.LEXICAL_FILENAME)
                        break

            if (terms_cf or phrases_cf) and info.headings:
                heading_set = frozenset(h.casefold() for h in info.headings)
                matched = False
                for qt in terms_cf:
                    if qt in heading_set:
                        matched = True
                        break
                if not matched:
                    for qp in phrases_cf:
                        if qp in heading_set:
                            matched = True
                            break
                if matched:
                    score += _HEADING_MATCH_WEIGHT
                    methods.append(RetrievalMethod.LEXICAL_HEADING)

            has_non_body_match = any(
                m
                for m in methods
                if m
                not in (
                    RetrievalMethod.LEXICAL_PHRASE,
                    RetrievalMethod.LEXICAL_BODY,
                )
            )
            needs_body = bool(phrases_cf) or (not has_non_body_match and bool(terms_cf))

            if needs_body:
                text = self._read_body(relative)
                if text is None:
                    continue
                body_cf = text.casefold()

                if phrases_cf:
                    for qp in phrases_cf:
                        if qp in body_cf:
                            score += _PHRASE_MATCH_WEIGHT
                            methods.append(RetrievalMethod.LEXICAL_PHRASE)
                            break

                if terms_cf and not any(
                    m
                    in (
                        RetrievalMethod.LEXICAL_PHRASE,
                        RetrievalMethod.LEXICAL_BODY,
                    )
                    for m in methods
                ):
                    for qt in terms_cf:
                        if qt in body_cf:
                            score += _BODY_TERM_MATCH_WEIGHT
                            methods.append(RetrievalMethod.LEXICAL_BODY)
                            break

            if score > 0 and methods:
                seen: set[RetrievalMethod] = set()
                deduped: list[RetrievalMethod] = []
                for m in methods:
                    if m not in seen:
                        seen.add(m)
                        deduped.append(m)
                scored.append(
                    (
                        -score,
                        relative,
                        MemoryCandidate(relative, info.title, tuple(deduped), score),
                    )
                )

        scored.sort(key=lambda x: (x[0], x[1]))
        results = [c[2] for c in scored[:limit]]
        return tuple(results)


def _glob_matches(path: str, pattern: str) -> bool:
    return fnmatchcase(path, pattern) or (
        pattern.startswith("**/") and fnmatchcase(path, pattern[3:])
    )
