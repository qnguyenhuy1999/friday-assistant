"""Persistence tests for memory index snapshots and retrieval audit records.

Built via `alembic upgrade head` (never `Base.metadata.create_all`), following
tests/persistence/test_migrations.py — this exercises the real migration 0007,
not just the ORM metadata."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from friday.application.memory.models import (
    IndexSnapshot,
    IndexState,
    MemoryRetrievalItem,
    MemoryRetrievalRecord,
    RetrievalMethod,
)
from friday.domain import Run, RunId, Task, TaskId
from friday.infrastructure.persistence.database import create_session_factory
from friday.infrastructure.persistence.repositories import (
    MemoryIndexSnapshotRepository,
    MemoryRetrievalRecordRepository,
    RunRepository,
    TaskRepository,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _alembic_config(db_path: Path) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


@pytest.fixture
def session(tmp_path: Path) -> Session:
    db_path = tmp_path / "memory.db"
    command.upgrade(_alembic_config(db_path), "head")
    engine = create_engine(f"sqlite:///{db_path}")
    factory = create_session_factory(engine)
    return factory()


def _make_run(session: Session) -> RunId:
    task = Task.new(id=TaskId.new(), title="t", description="d", created_at=T0)
    TaskRepository(session).add(task)
    session.flush()
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
    RunRepository(session).add(run)
    session.flush()
    return run.id


def _make_snapshot(
    snapshot_id: str, *, built_at: datetime = T0, state: IndexState = IndexState.FRESH
) -> IndexSnapshot:
    return IndexSnapshot(
        id=snapshot_id,
        vault_identity_hash="vault-1",
        source_snapshot_hash="src-hash-1",
        graph_checksum="graph-checksum-1",
        graphify_version="0.9.22",
        state=state,
        built_at=built_at,
        build_duration_seconds=1.5,
        file_count=10,
        source_total_bytes=1000,
        node_count=5,
        edge_count=4,
        failure_code=None,
    )


def _make_item(rank: int) -> MemoryRetrievalItem:
    return MemoryRetrievalItem(
        path="notes/a.md",
        heading="Heading",
        start_line=1,
        end_line=2,
        content_hash="deadbeef",
        rank=rank,
        methods=(RetrievalMethod.LEXICAL_TITLE,),
        truncated=False,
    )


def _make_record(
    record_id: str, run_id: RunId, *, items: tuple[MemoryRetrievalItem, ...] = ()
) -> MemoryRetrievalRecord:
    return MemoryRetrievalRecord(
        id=record_id,
        run_id=run_id,
        turn_number=1,
        query_hash="query-hash-1",
        source_snapshot_id="src-hash-1",
        index_snapshot_id=None,
        created_at=T0,
        candidate_count=3,
        selected_count=len(items),
        items=items,
    )


def test_upgrade_creates_memory_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    command.upgrade(_alembic_config(db_path), "head")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        tables = set(inspect(engine).get_table_names())
        assert {
            "memory_index_snapshots",
            "memory_retrieval_records",
            "memory_retrieval_items",
        } <= tables
    finally:
        engine.dispose()


def test_downgrade_then_upgrade_drops_and_recreates_memory_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    config = _alembic_config(db_path)
    command.upgrade(config, "head")
    command.downgrade(config, "0006")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        tables = set(inspect(engine).get_table_names())
        assert "memory_index_snapshots" not in tables
        assert "memory_retrieval_records" not in tables
        assert "memory_retrieval_items" not in tables
    finally:
        engine.dispose()
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        assert "memory_index_snapshots" in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_no_excerpt_body_column_exists(tmp_path: Path) -> None:
    """Hard security rule: never store excerpt bodies, absolute paths, or query text."""
    db_path = tmp_path / "migrate.db"
    command.upgrade(_alembic_config(db_path), "head")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        inspector = inspect(engine)
        forbidden = {"text", "body", "content", "excerpt", "query", "query_text"}
        for table_name in (
            "memory_index_snapshots",
            "memory_retrieval_records",
            "memory_retrieval_items",
        ):
            columns = {c["name"] for c in inspector.get_columns(table_name)}
            assert not (columns & forbidden), f"{table_name} exposes forbidden column(s)"
    finally:
        engine.dispose()


def test_snapshot_add_then_latest_round_trips(session: Session) -> None:
    repo = MemoryIndexSnapshotRepository(session)
    repo.add(_make_snapshot("snap-1"))
    session.flush()
    latest = repo.latest()
    assert latest is not None
    assert latest.id == "snap-1"
    assert latest.state == IndexState.FRESH


def test_latest_returns_none_when_no_snapshots(session: Session) -> None:
    repo = MemoryIndexSnapshotRepository(session)
    assert repo.latest() is None


def test_latest_returns_most_recently_built_snapshot(session: Session) -> None:
    repo = MemoryIndexSnapshotRepository(session)
    repo.add(_make_snapshot("snap-old", built_at=T0))
    repo.add(_make_snapshot("snap-new", built_at=T0 + timedelta(hours=1)))
    session.flush()
    latest = repo.latest()
    assert latest is not None
    assert latest.id == "snap-new"


def test_mark_stale_updates_snapshot_status(session: Session) -> None:
    repo = MemoryIndexSnapshotRepository(session)
    repo.add(_make_snapshot("snap-1", state=IndexState.FRESH))
    session.flush()
    repo.mark_stale("snap-1")
    session.flush()
    session.expire_all()
    latest = repo.latest()
    assert latest is not None
    assert latest.state == IndexState.STALE


def test_retrieval_record_add_then_get_round_trips_with_items(session: Session) -> None:
    run_id = _make_run(session)
    repo = MemoryRetrievalRecordRepository(session)
    items = (_make_item(0), _make_item(1))
    record = _make_record("rec-1", run_id, items=items)
    repo.add(record)
    session.flush()
    fetched = repo.get("rec-1")
    assert fetched is not None
    assert fetched.run_id == run_id
    assert len(fetched.items) == 2
    assert [item.rank for item in fetched.items] == [0, 1]


def test_retrieval_record_get_returns_none_for_missing_id(session: Session) -> None:
    repo = MemoryRetrievalRecordRepository(session)
    assert repo.get("missing") is None


def test_retrieval_record_add_enforces_items_cap(session: Session) -> None:
    run_id = _make_run(session)
    repo = MemoryRetrievalRecordRepository(session)
    too_many_items = tuple(_make_item(rank) for rank in range(51))
    record = _make_record("rec-cap", run_id, items=too_many_items)
    with pytest.raises(ValueError, match="exceeds"):
        repo.add(record)


def test_rollback_after_retrieval_record_add_leaves_no_rows(session: Session) -> None:
    run_id = _make_run(session)
    session.commit()
    repo = MemoryRetrievalRecordRepository(session)
    repo.add(_make_record("rec-rollback", run_id, items=(_make_item(0),)))
    session.flush()
    session.rollback()
    assert repo.get("rec-rollback") is None


def test_rollback_after_snapshot_add_leaves_no_rows(session: Session) -> None:
    session.commit()
    repo = MemoryIndexSnapshotRepository(session)
    repo.add(_make_snapshot("snap-rollback"))
    session.flush()
    session.rollback()
    assert repo.latest() is None
