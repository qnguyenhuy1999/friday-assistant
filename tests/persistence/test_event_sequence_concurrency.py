from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from friday.domain import Run, RunId, Task, TaskId
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.models import Base
from friday.infrastructure.persistence.repositories import (
    RunEventStore,
    RunRepository,
    TaskEventStore,
    TaskRepository,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _make_run(session: Session) -> tuple[TaskId, RunId]:
    task = Task.new(id=TaskId.new(), title="t", description="d", created_at=T0)
    TaskRepository(session).add(task)
    session.flush()
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
    RunRepository(session).add(run)
    session.flush()
    return task.id, run.id


def _sessions(tmp_path: Path) -> tuple[Session, Session, Session, Engine]:
    engine = create_engine(f"sqlite:///{tmp_path / 'event-sequences.db'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    return factory(), factory(), factory(), engine


def test_run_event_reservations_do_not_overlap_across_sessions(tmp_path: Path) -> None:
    seed, session_a, session_b, engine = _sessions(tmp_path)
    try:
        _, run_id = _make_run(seed)
        seed.commit()

        assert RunEventStore(session_a).reserve_sequences(run_id, 1) == 1
        session_a.commit()
        assert RunEventStore(session_b).reserve_sequences(run_id, 1) == 2
        session_b.commit()
    finally:
        seed.close()
        session_a.close()
        session_b.close()
        engine.dispose()


def test_run_event_block_reservations_are_contiguous(tmp_path: Path) -> None:
    seed, session_a, session_b, engine = _sessions(tmp_path)
    try:
        _, run_id = _make_run(seed)
        seed.commit()

        first = RunEventStore(session_a).reserve_sequences(run_id, 3)
        session_a.commit()
        second = RunEventStore(session_b).reserve_sequences(run_id, 2)
        session_b.commit()

        assert list(range(first, first + 3)) == [1, 2, 3]
        assert list(range(second, second + 2)) == [4, 5]
    finally:
        seed.close()
        session_a.close()
        session_b.close()
        engine.dispose()


def test_rolled_back_run_event_reservation_does_not_consume_a_range(tmp_path: Path) -> None:
    seed, session_a, session_b, engine = _sessions(tmp_path)
    try:
        _, run_id = _make_run(seed)
        seed.commit()

        assert RunEventStore(session_a).reserve_sequences(run_id, 3) == 1
        session_a.rollback()
        assert RunEventStore(session_b).reserve_sequences(run_id, 1) == 1
        session_b.commit()
    finally:
        seed.close()
        session_a.close()
        session_b.close()
        engine.dispose()


def test_task_event_reservations_do_not_overlap_and_rollback_is_reusable(tmp_path: Path) -> None:
    seed, session_a, session_b, engine = _sessions(tmp_path)
    try:
        task_id, _ = _make_run(seed)
        seed.commit()

        assert TaskEventStore(session_a).reserve_sequences(task_id, 2) == 1
        session_a.commit()
        assert TaskEventStore(session_b).reserve_sequences(task_id, 1) == 3
        session_b.rollback()
        assert TaskEventStore(session_a).reserve_sequences(task_id, 1) == 3
        session_a.commit()
    finally:
        seed.close()
        session_a.close()
        session_b.close()
        engine.dispose()
