from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from friday.domain import Failure, FailureCause, Task, TaskId
from friday.infrastructure.persistence.repositories import TaskRepository

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_add_then_get_round_trips(session: Session) -> None:
    repo = TaskRepository(session)
    task = Task.new(id=TaskId.new(), title="t", description="d", created_at=T0)
    repo.add(task)
    session.flush()
    fetched = repo.get(task.id)
    assert fetched is not None
    assert fetched.id == task.id
    assert fetched.title == "t"


def test_get_returns_none_for_missing_id(session: Session) -> None:
    repo = TaskRepository(session)
    assert repo.get(TaskId.new()) is None


def test_save_persists_status_transition(session: Session) -> None:
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


def test_timestamps_are_tz_aware_utc_after_db_round_trip(session: Session) -> None:
    repo = TaskRepository(session)
    task = Task.new(id=TaskId.new(), title="t", description="d", created_at=T0)
    task.start(T0)
    task.fail(T0, Failure(code="E", message="m", retryable=False, cause=FailureCause.RUNTIME))
    repo.add(task)
    session.flush()
    session.expire_all()  # force re-read from SQLite, not the identity map
    fetched = repo.get(task.id)
    assert fetched is not None
    assert fetched.created_at == T0
    assert fetched.created_at.tzinfo is UTC
    assert fetched.started_at == T0
    assert fetched.started_at.tzinfo is UTC
    assert fetched.failed_at == T0
    assert fetched.failed_at.tzinfo is UTC
