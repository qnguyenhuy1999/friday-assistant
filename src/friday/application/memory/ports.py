"""Application-owned memory ports. Infrastructure implements these against
the real vault filesystem and Graphify; the application layer only sees the
models in friday.application.memory.models. MemoryRetrieverPort is the
single interface AgentRunProcessor depends on — everything else here is
implementation detail behind it."""

from __future__ import annotations

from typing import Protocol

from friday.application.memory.models import (
    IndexBuildRequest,
    IndexSnapshot,
    IndexStatus,
    MemoryCandidate,
    MemoryContext,
    MemoryExcerpt,
    MemoryQuery,
    MemoryRetrievalRecord,
    MemoryWriteCandidate,
    MemoryWriteResult,
)


class MemoryStore(Protocol):
    def search_lexical(self, query: MemoryQuery, *, limit: int) -> tuple[MemoryCandidate, ...]: ...

    def read_excerpt(self, candidate: MemoryCandidate, *, max_chars: int) -> MemoryExcerpt: ...

    def write_candidate(self, candidate: MemoryWriteCandidate) -> MemoryWriteResult: ...

    def source_snapshot_hash(self) -> str: ...


class StructuralIndex(Protocol):
    def status(self) -> IndexStatus: ...

    def search(self, query: MemoryQuery, *, limit: int) -> tuple[MemoryCandidate, ...]: ...

    def neighbors(
        self, path: str, *, depth: int, max_nodes: int
    ) -> tuple[MemoryCandidate, ...]: ...


class StructuralIndexBuilder(Protocol):
    def build(self, request: IndexBuildRequest) -> IndexSnapshot: ...


class MemoryIndexSnapshotRepository(Protocol):
    def add(self, snapshot: IndexSnapshot) -> None: ...
    def latest(self) -> IndexSnapshot | None: ...
    def mark_stale(self, snapshot_id: str) -> None: ...


class MemoryRetrievalRecordRepository(Protocol):
    def add(self, record: MemoryRetrievalRecord) -> None: ...


class MemoryRetrieverPort(Protocol):
    def retrieve(self, *, query: MemoryQuery) -> MemoryContext: ...
