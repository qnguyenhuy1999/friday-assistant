from __future__ import annotations

from datetime import UTC, datetime

from friday.domain import Task, TaskId
from friday.infrastructure.persistence.repositories import TaskRepository

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_add_then_get_round_trips(session) -> None:
    repo = TaskRepository(session)
    task = Task.new(id=TaskId.new(), title="t", description="d", created_at=T0)
    repo.add(task)
    session.flush()
    fetched = repo.get(task.id)
    assert fetched is not None
    assert fetched.id == task.id
    assert fetched.title == "t"


def test_get_returns_none_for_missing_id(session) -> None:
    repo = TaskRepository(session)
    assert repo.get(TaskId.new()) is None


def test_save_persists_status_transition(session) -> None:
    repo = TaskRepository(session)
    task = Task.new(id=TaskId.new(), title="t", description="d", created_at=T0)
    repo.add(task)
    session.flush()
    task.start(T0)
    repo.save(task)
    session.flush()
    fetched = repo.get(task.id)
    assert fetched is not None
    assert fetched.status == task.status
