"""Use cases for derived memory-index lifecycle management.

The coordinator deliberately keeps index construction outside persistence
transactions: the index is disposable and building it may take minutes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from friday.application.memory.models import (
    IndexBuildRequest,
    IndexSnapshot,
    IndexState,
    IndexStatus,
)
from friday.application.memory.ports import (
    MemoryIndexSnapshotRepository,
    MemoryStore,
    StructuralIndex,
    StructuralIndexBuilder,
)


@dataclass(frozen=True, slots=True)
class InspectMemoryIndex:
    index: StructuralIndex
    store: MemoryStore

    def execute(self) -> IndexStatus:
        status = self.index.status()
        if (
            status.state is IndexState.FRESH
            and status.source_snapshot_hash != self.store.source_snapshot_hash()
        ):
            return IndexStatus(
                IndexState.STALE,
                status.snapshot_id,
                status.source_snapshot_hash,
                status.graph_checksum,
                status.node_count,
                status.edge_count,
                status.built_at,
                "snapshot_mismatch",
            )
        return status


@dataclass(frozen=True, slots=True)
class BuildMemoryIndex:
    builder: StructuralIndexBuilder
    store: MemoryStore
    request_factory: Callable[[str], IndexBuildRequest]
    snapshots: MemoryIndexSnapshotRepository | None = None

    def execute(self) -> IndexSnapshot:
        source_hash = self.store.source_snapshot_hash()
        # No UnitOfWork is opened around this call: builders own their atomic lock/promotion.
        snapshot = self.builder.build(self.request_factory(source_hash))
        if self.snapshots is not None:
            self.snapshots.add(snapshot)
        return snapshot


@dataclass(frozen=True, slots=True)
class RefreshMemoryIndexIfStale:
    inspect: InspectMemoryIndex
    build: BuildMemoryIndex

    def execute(self) -> IndexSnapshot | None:
        status = self.inspect.execute()
        if status.state not in {IndexState.STALE, IndexState.MISSING, IndexState.CORRUPT}:
            return None
        return self.build.execute()


@dataclass(frozen=True, slots=True)
class QuarantineCorruptMemoryIndex:
    index: StructuralIndex
    quarantine: Callable[[], bool]

    def execute(self) -> bool:
        """Quarantine only derived corrupt data; callers schedule a later rebuild."""
        if self.index.status().state is not IndexState.CORRUPT:
            return False
        return self.quarantine()
