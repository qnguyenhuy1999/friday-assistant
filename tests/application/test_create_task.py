"""CreateTask unit tests with controlled dependencies (fake UoW + Clock)."""

from __future__ import annotations

import dataclasses

import pytest

from friday.application.commands import CreateTaskCommand
from friday.application.create_task import CreateTask
from friday.application.results import CreateTaskResult
from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import TaskId
from friday.domain.task import Task, TaskStatus
from tests.application.fakes import (
    T0,
    CountingUnitOfWorkFactory,
    FakeClock,
    FakeUnitOfWork,
)


def test_persists_valid_task_with_allocated_id_and_clock_timestamp(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    result = CreateTask(uow_factory, clock).execute(
        CreateTaskCommand(title="  Ship it  ", description=" details ")
    )

    task = fake_uow.task_repo.get(result.task_id)
    assert task is not None
    assert isinstance(task.id, TaskId)
    assert task.title == "Ship it"
    assert task.description == "details"
    assert task.status is TaskStatus.PENDING
    assert task.created_at == T0


def test_allocates_a_distinct_id_per_invocation(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    use_case = CreateTask(uow_factory, clock)
    first = use_case.execute(CreateTaskCommand(title="a", description=""))
    second = use_case.execute(CreateTaskCommand(title="b", description=""))
    assert first.task_id != second.task_id


def test_commits_exactly_once_and_never_rolls_back_on_success(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    CreateTask(uow_factory, clock).execute(CreateTaskCommand(title="t", description=""))
    assert fake_uow.commit_count == 1
    assert fake_uow.rollback_count == 0
    assert fake_uow.closed


def test_persistence_failure_triggers_rollback_and_no_commit(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    def failing_add(task: Task) -> None:
        raise RuntimeError("staging failed")

    fake_uow.task_repo.add = failing_add  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        CreateTask(uow_factory, clock).execute(CreateTaskCommand(title="t", description=""))

    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 1
    assert fake_uow.closed


def test_invalid_title_fails_before_any_unit_of_work_is_opened(
    uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    with pytest.raises(DomainValidationError):
        CreateTask(uow_factory, clock).execute(CreateTaskCommand(title="   ", description=""))
    assert uow_factory.calls == 0


def test_command_and_result_are_plain_frozen_dataclasses(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    command = CreateTaskCommand(title="t", description="")
    result = CreateTask(uow_factory, clock).execute(command)

    assert dataclasses.is_dataclass(command) and dataclasses.is_dataclass(result)
    assert isinstance(result, CreateTaskResult)
    assert isinstance(result.task_id, TaskId)
    with pytest.raises(dataclasses.FrozenInstanceError):
        command.title = "mutated"  # type: ignore[misc]
