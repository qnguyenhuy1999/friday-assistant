"""Task lifecycle: allowed/forbidden transitions, terminal-state rejection,
field normalization."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import TaskId
from friday.domain.task import TERMINAL_TASK_STATUSES, Task, TaskStatus

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 1, 1, tzinfo=UTC)

ALL_STATUSES = list(TaskStatus)


def _new_task() -> Task:
    return Task.new(
        id=TaskId.new(), title="  Do the thing  ", description="  desc  ", created_at=T0
    )


def _failure() -> Failure:
    return Failure(code="x", message="boom", retryable=False, cause=FailureCause.INTERNAL)


def _task_in(status: TaskStatus) -> Task:
    task = _new_task()
    if status is TaskStatus.PENDING:
        return task
    task.start(T0)
    if status is TaskStatus.ACTIVE:
        return task
    if status is TaskStatus.COMPLETED:
        task.complete(T1)
    elif status is TaskStatus.FAILED:
        task.fail(T1, _failure())
    elif status is TaskStatus.CANCELLED:
        task = _new_task()
        task.cancel(T0)
    return task


def test_new_strips_title_and_description() -> None:
    task = _new_task()
    assert task.title == "Do the thing"
    assert task.description == "desc"
    assert task.status is TaskStatus.PENDING


def test_new_rejects_blank_title() -> None:
    with pytest.raises(DomainValidationError):
        Task.new(id=TaskId.new(), title="   ", description="d", created_at=T0)


def test_start_from_pending_succeeds() -> None:
    task = _task_in(TaskStatus.PENDING)
    task.start(T1)
    assert task.status is TaskStatus.ACTIVE
    assert task.started_at == T1


@pytest.mark.parametrize("status", [s for s in ALL_STATUSES if s != TaskStatus.PENDING])
def test_start_from_non_pending_is_rejected(status: TaskStatus) -> None:
    task = _task_in(status)
    with pytest.raises(InvalidStateTransition):
        task.start(T1)


def test_complete_from_active_succeeds() -> None:
    task = _task_in(TaskStatus.ACTIVE)
    task.complete(T1)
    assert task.status is TaskStatus.COMPLETED
    assert task.completed_at == T1


@pytest.mark.parametrize("status", [s for s in ALL_STATUSES if s != TaskStatus.ACTIVE])
def test_complete_from_non_active_is_rejected(status: TaskStatus) -> None:
    task = _task_in(status)
    with pytest.raises(InvalidStateTransition):
        task.complete(T1)


def test_fail_from_active_succeeds() -> None:
    task = _task_in(TaskStatus.ACTIVE)
    failure = _failure()
    task.fail(T1, failure)
    assert task.status is TaskStatus.FAILED
    assert task.failure is failure


@pytest.mark.parametrize("status", [s for s in ALL_STATUSES if s != TaskStatus.ACTIVE])
def test_fail_from_non_active_is_rejected(status: TaskStatus) -> None:
    task = _task_in(status)
    with pytest.raises(InvalidStateTransition):
        task.fail(T1, _failure())


@pytest.mark.parametrize("status", [TaskStatus.PENDING, TaskStatus.ACTIVE])
def test_cancel_from_pending_or_active_succeeds(status: TaskStatus) -> None:
    task = _task_in(status)
    task.cancel(T1)
    assert task.status is TaskStatus.CANCELLED


@pytest.mark.parametrize("status", sorted(TERMINAL_TASK_STATUSES, key=str))
def test_cancel_from_terminal_status_is_rejected(status: TaskStatus) -> None:
    task = _task_in(status)
    with pytest.raises(InvalidStateTransition):
        task.cancel(T1)


@pytest.mark.parametrize("status", sorted(TERMINAL_TASK_STATUSES, key=str))
def test_terminal_statuses_are_actually_terminal(status: TaskStatus) -> None:
    task = _task_in(status)
    with pytest.raises(InvalidStateTransition):
        task.start(T1)
    with pytest.raises(InvalidStateTransition):
        task.complete(T1)
    with pytest.raises(InvalidStateTransition):
        task.fail(T1, _failure())
    with pytest.raises(InvalidStateTransition):
        task.cancel(T1)
