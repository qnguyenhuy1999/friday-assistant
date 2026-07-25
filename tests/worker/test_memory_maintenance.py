from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from friday.application.memory.index_coordination import (
    BuildMemoryIndex,
    InspectMemoryIndex,
    QuarantineCorruptMemoryIndex,
    RefreshMemoryIndexIfStale,
)
from friday.application.memory.models import (
    IndexBuildRequest,
    IndexSnapshot,
    IndexState,
    IndexStatus,
    MemoryCandidate,
    MemoryExcerpt,
    MemoryQuery,
    MemoryWriteCandidate,
    MemoryWriteResult,
)


@dataclass
class Store:
    value: str = "now"

    def source_snapshot_hash(self) -> str:
        return self.value

    def search_lexical(self, query: MemoryQuery, *, limit: int) -> tuple[MemoryCandidate, ...]:
        return ()

    def read_excerpt(self, candidate: MemoryCandidate, *, max_chars: int) -> MemoryExcerpt:
        raise AssertionError("not called")

    def write_candidate(self, candidate: MemoryWriteCandidate) -> MemoryWriteResult:
        raise AssertionError("not called")


@dataclass
class Index:
    value: IndexStatus

    def status(self) -> IndexStatus:
        return self.value

    def search(self, query: MemoryQuery, *, limit: int) -> tuple[MemoryCandidate, ...]:
        return ()

    def neighbors(self, path: str, *, depth: int, max_nodes: int) -> tuple[MemoryCandidate, ...]:
        return ()


class Builder:
    calls = 0

    def build(self, request: IndexBuildRequest) -> IndexSnapshot:
        self.calls += 1
        return IndexSnapshot(
            "id",
            "vault",
            request.source_snapshot_hash,
            None,
            None,
            IndexState.FRESH,
            datetime.now(UTC),
            0,
            0,
            0,
            0,
            0,
            None,
        )


def _status(state: IndexState, source: str | None = "now") -> IndexStatus:
    return IndexStatus(state, None, source, None, 0, 0, None, None)


def _request(source: str) -> IndexBuildRequest:
    return IndexBuildRequest("vault", source, (), 1, 1)


def test_refresh_triggered_by_snapshot_mismatch() -> None:
    builder = Builder()
    refresh = RefreshMemoryIndexIfStale(
        InspectMemoryIndex(Index(_status(IndexState.FRESH, "old")), Store()),
        BuildMemoryIndex(builder, Store(), _request),
    )
    assert refresh.execute() is not None
    assert builder.calls == 1


def test_no_rebuild_when_index_is_fresh() -> None:
    builder = Builder()
    refresh = RefreshMemoryIndexIfStale(
        InspectMemoryIndex(Index(_status(IndexState.FRESH)), Store()),
        BuildMemoryIndex(builder, Store(), _request),
    )
    assert refresh.execute() is None
    assert builder.calls == 0


def test_corrupt_index_is_quarantined() -> None:
    calls = 0

    def quarantine() -> bool:
        nonlocal calls
        calls += 1
        return True

    assert QuarantineCorruptMemoryIndex(Index(_status(IndexState.CORRUPT)), quarantine).execute()
    assert calls == 1


def test_healthy_index_is_not_quarantined() -> None:
    assert not QuarantineCorruptMemoryIndex(
        Index(_status(IndexState.FRESH)), lambda: (_ for _ in ()).throw(AssertionError())
    ).execute()
