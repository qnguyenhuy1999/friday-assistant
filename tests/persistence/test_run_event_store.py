from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from friday.domain import Run, RunEvent, RunEventId, RunEventType, RunId, Task, TaskId
from friday.infrastructure.persistence.repositories import (
    RunEventStore,
    RunRepository,
    TaskRepository,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _make_run(session: Session) -> RunId:
    task = Task.new(id=TaskId.new(), title="t", description="d", created_at=T0)
    TaskRepository(session).add(task)
    session.flush()
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
    RunRepository(session).add(run)
    session.flush()
    return run.id


def test_next_sequence_starts_at_one_for_a_new_run(session: Session) -> None:
    store = RunEventStore(session)
    run_id = _make_run(session)
    assert store.next_sequence(run_id) == 1


def test_next_sequence_increments_after_append(session: Session) -> None:
    store = RunEventStore(session)
    run_id = _make_run(session)
    store.append(
        RunEvent(
            id=RunEventId.new(),
            run_id=run_id,
            type=RunEventType.RUN_CREATED,
            sequence=1,
            occurred_at=T0,
        )
    )
    session.flush()
    assert store.next_sequence(run_id) == 2


def test_list_for_run_orders_by_sequence(session: Session) -> None:
    store = RunEventStore(session)
    run_id = _make_run(session)
    store.append(
        RunEvent(
            id=RunEventId.new(),
            run_id=run_id,
            type=RunEventType.RUN_STARTED,
            sequence=2,
            occurred_at=T0,
        )
    )
    store.append(
        RunEvent(
            id=RunEventId.new(),
            run_id=run_id,
            type=RunEventType.RUN_CREATED,
            sequence=1,
            occurred_at=T0,
        )
    )
    session.flush()
    result = store.list_for_run(run_id)
    assert [e.sequence for e in result] == [1, 2]


def test_duplicate_sequence_for_same_run_is_rejected_at_db_level(session: Session) -> None:
    store = RunEventStore(session)
    run_id = _make_run(session)
    store.append(
        RunEvent(
            id=RunEventId.new(),
            run_id=run_id,
            type=RunEventType.RUN_CREATED,
            sequence=1,
            occurred_at=T0,
        )
    )
    session.flush()
    store.append(
        RunEvent(
            id=RunEventId.new(),
            run_id=run_id,
            type=RunEventType.RUN_STARTED,
            sequence=1,
            occurred_at=T0,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
