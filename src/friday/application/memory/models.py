"""Application-owned memory models: immutable value objects for Obsidian
retrieval, provenance, and writes. No Path, no file descriptors, no
subprocess results, no Graphify JSON shapes, no SQLAlchemy — paths crossing
this boundary are normalized vault-relative POSIX strings (see
.herdr/phase12-invariants.md)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from friday.domain.identifiers import RunId
from friday.domain.time import ensure_utc


class RetrievalMethod(StrEnum):
    LEXICAL_TITLE = "lexical_title"
    LEXICAL_ALIAS = "lexical_alias"
    LEXICAL_TAG = "lexical_tag"
    LEXICAL_HEADING = "lexical_heading"
    LEXICAL_FILENAME = "lexical_filename"
    LEXICAL_PHRASE = "lexical_phrase"
    LEXICAL_BODY = "lexical_body"
    STRUCTURAL_NODE = "structural_node"
    STRUCTURAL_NEIGHBOR = "structural_neighbor"
    STRUCTURAL_BACKLINK = "structural_backlink"


class RetrievalMode(StrEnum):
    HYBRID = "hybrid"
    LEXICAL_ONLY = "lexical_only"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"


class IndexState(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"
    CORRUPT = "corrupt"
    DISABLED = "disabled"


class MemoryWriteOperation(StrEnum):
    CREATE_NOTE = "create_note"
    APPEND_MANAGED_NOTE = "append_managed_note"


def _ensure_relative_path(path: str, *, field_name: str) -> None:
    """Shared path guard for every model that carries a vault-relative
    location: no absolute paths, no `..` traversal, never empty."""
    if not path:
        raise ValueError(f"{field_name} must not be empty")
    if path.startswith("/") or path.startswith("~"):
        raise ValueError(f"{field_name} must not be an absolute path")
    if any(part == ".." for part in path.split("/")):
        raise ValueError(f"{field_name} must not contain '..' components")


@dataclass(frozen=True, slots=True)
class MemoryVaultPolicy:
    include_globs: tuple[str, ...]
    exclude_globs: tuple[str, ...]
    max_files: int
    max_note_bytes: int

    def __post_init__(self) -> None:
        if not self.include_globs:
            raise ValueError("MemoryVaultPolicy.include_globs must not be empty")
        if self.max_files <= 0:
            raise ValueError("MemoryVaultPolicy.max_files must be positive")
        if self.max_note_bytes <= 0:
            raise ValueError("MemoryVaultPolicy.max_note_bytes must be positive")


@dataclass(frozen=True, slots=True)
class MemoryQuery:
    terms: tuple[str, ...] = ()
    phrases: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    titles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not (self.terms or self.phrases or self.tags or self.titles):
            raise ValueError("MemoryQuery must not be wholly empty")

    @property
    def query_hash(self) -> str:
        """Deterministic across field order and case: each field is
        case-folded and sorted before hashing, so two queries naming the
        same terms in a different order or casing hash identically."""
        normalized = {
            "terms": sorted({term.casefold() for term in self.terms}),
            "phrases": sorted({phrase.casefold() for phrase in self.phrases}),
            "tags": sorted({tag.casefold() for tag in self.tags}),
            "titles": sorted({title.casefold() for title in self.titles}),
        }
        canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    path: str
    title: str
    methods: tuple[RetrievalMethod, ...]
    score: float
    headings: tuple[str, ...] = ()
    graph_distance: int | None = None
    index_snapshot_id: str | None = None

    def __post_init__(self) -> None:
        _ensure_relative_path(self.path, field_name="MemoryCandidate.path")
        if not self.methods:
            raise ValueError("MemoryCandidate.methods must not be empty")
        if self.score < 0:
            raise ValueError("MemoryCandidate.score must not be negative")
        if self.graph_distance is not None and self.graph_distance < 0:
            raise ValueError("MemoryCandidate.graph_distance must not be negative")


@dataclass(frozen=True, slots=True)
class MemoryExcerpt:
    path: str
    title: str
    heading: str | None
    start_line: int
    end_line: int
    text: str
    content_hash: str
    truncated: bool

    def __post_init__(self) -> None:
        _ensure_relative_path(self.path, field_name="MemoryExcerpt.path")
        if self.start_line < 1:
            raise ValueError("MemoryExcerpt.start_line must be >= 1")
        if self.end_line < self.start_line:
            raise ValueError("MemoryExcerpt.end_line must be >= start_line")


@dataclass(frozen=True, slots=True)
class MemoryProvenance:
    path: str
    title: str
    heading: str | None
    start_line: int
    end_line: int
    content_hash: str
    methods: tuple[RetrievalMethod, ...]
    rank: int
    index_snapshot_id: str | None
    source_snapshot_id: str
    truncated: bool

    def __post_init__(self) -> None:
        _ensure_relative_path(self.path, field_name="MemoryProvenance.path")
        if self.start_line < 1:
            raise ValueError("MemoryProvenance.start_line must be >= 1")
        if self.end_line < self.start_line:
            raise ValueError("MemoryProvenance.end_line must be >= start_line")
        if not self.methods:
            raise ValueError("MemoryProvenance.methods must not be empty")
        if self.rank < 0:
            raise ValueError("MemoryProvenance.rank must not be negative")


@dataclass(frozen=True, slots=True)
class MemoryContext:
    mode: RetrievalMode
    excerpts: tuple[MemoryExcerpt, ...]
    provenance: tuple[MemoryProvenance, ...]
    degraded_reason: str | None
    index_state: IndexState
    total_chars: int
    # Snapshot that supplied the structural portion of this retrieval.  This
    # is deliberately retrieval-level provenance: the highest-ranked excerpt
    # can be lexical-only even when a structural index influenced ranking.
    index_snapshot_id: str | None = None

    def __post_init__(self) -> None:
        if len(self.excerpts) != len(self.provenance):
            raise ValueError("MemoryContext.excerpts and .provenance must be the same length")
        for excerpt, provenance in zip(self.excerpts, self.provenance, strict=True):
            if excerpt.path != provenance.path:
                raise ValueError(
                    "MemoryContext.excerpts and .provenance must share identical paths in "
                    "identical order"
                )


@dataclass(frozen=True, slots=True)
class MemoryWriteCandidate:
    operation: MemoryWriteOperation
    path: str
    observed_content_hash: str | None
    payload: str
    frontmatter: tuple[tuple[str, str], ...]
    memory_category: str

    def __post_init__(self) -> None:
        _ensure_relative_path(self.path, field_name="MemoryWriteCandidate.path")
        if (
            self.operation is MemoryWriteOperation.APPEND_MANAGED_NOTE
            and self.observed_content_hash is None
        ):
            raise ValueError(
                "MemoryWriteCandidate.observed_content_hash is required for append_managed_note"
            )
        if (
            self.operation is MemoryWriteOperation.CREATE_NOTE
            and self.observed_content_hash is not None
        ):
            raise ValueError(
                "MemoryWriteCandidate.observed_content_hash must be None for create_note"
            )


@dataclass(frozen=True, slots=True)
class MemoryWriteResult:
    path: str
    operation: MemoryWriteOperation
    content_hash: str
    created: bool
    bytes_written: int


@dataclass(frozen=True, slots=True)
class IndexStatus:
    state: IndexState
    snapshot_id: str | None
    source_snapshot_hash: str | None
    graph_checksum: str | None
    node_count: int
    edge_count: int
    built_at: datetime | None
    failure_code: str | None

    def __post_init__(self) -> None:
        if self.built_at is not None:
            object.__setattr__(self, "built_at", ensure_utc(self.built_at))


@dataclass(frozen=True, slots=True)
class IndexBuildRequest:
    vault_identity_hash: str
    source_snapshot_hash: str
    included_paths: tuple[str, ...]
    timeout_seconds: float
    max_graph_bytes: int


@dataclass(frozen=True, slots=True)
class IndexSnapshot:
    id: str
    vault_identity_hash: str
    source_snapshot_hash: str
    graph_checksum: str | None
    graphify_version: str | None
    state: IndexState
    built_at: datetime
    build_duration_seconds: float
    file_count: int
    source_total_bytes: int
    node_count: int
    edge_count: int
    failure_code: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "built_at", ensure_utc(self.built_at))


@dataclass(frozen=True, slots=True)
class MemoryRetrievalItem:
    path: str
    heading: str | None
    start_line: int
    end_line: int
    content_hash: str
    rank: int
    methods: tuple[RetrievalMethod, ...]
    truncated: bool

    def __post_init__(self) -> None:
        _ensure_relative_path(self.path, field_name="MemoryRetrievalItem.path")
        if self.start_line < 1:
            raise ValueError("MemoryRetrievalItem.start_line must be >= 1")
        if self.end_line < self.start_line:
            raise ValueError("MemoryRetrievalItem.end_line must be >= start_line")


@dataclass(frozen=True, slots=True)
class MemoryRetrievalRecord:
    id: str
    run_id: RunId
    turn_number: int
    query_hash: str
    source_snapshot_id: str | None
    index_snapshot_id: str | None
    created_at: datetime
    candidate_count: int
    selected_count: int
    items: tuple[MemoryRetrievalItem, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
