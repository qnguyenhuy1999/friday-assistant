from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from friday.domain import Run, RunId, Task, TaskId
from friday.infrastructure.persistence.repositories import RunRepository, TaskRepository

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _make_task(session: Session) -> TaskId:
    task = Task.new(id=TaskId.new(), title="t", description="d", created_at=T0)
    TaskRepository(session).add(task)
    session.flush()
    return task.id


def test_add_then_get_round_trips(session: Session) -> None:
    task_id = _make_task(session)
    repo = RunRepository(session)
    run = Run.new(id=RunId.new(), task_id=task_id, created_at=T0)
    repo.add(run)
    session.flush()
    fetched = repo.get(run.id)
    assert fetched is not None
    assert fetched.id == run.id
    assert fetched.task_id == run.task_id


def test_get_returns_none_for_missing_id(session: Session) -> None:
    repo = RunRepository(session)
    assert repo.get(RunId.new()) is None


def test_save_persists_status_transition(session: Session) -> None:
    task_id = _make_task(session)
    repo = RunRepository(session)
    run = Run.new(id=RunId.new(), task_id=task_id, created_at=T0)
    repo.add(run)
    session.flush()
    run.start(T0)
    repo.save(run)
    session.flush()
    fetched = repo.get(run.id)
    assert fetched is not None
    assert fetched.status == run.status


def test_list_for_task_orders_by_created_at_then_id(session: Session) -> None:
    task_id = _make_task(session)
    repo = RunRepository(session)
    run_a = Run.new(id=RunId.new(), task_id=task_id, created_at=T0)
    run_b = Run.new(id=RunId.new(), task_id=task_id, created_at=T0 + timedelta(seconds=1))
    repo.add(run_b)
    repo.add(run_a)
    session.flush()
    result = repo.list_for_task(task_id)
    assert [r.id for r in result] == [run_a.id, run_b.id]
