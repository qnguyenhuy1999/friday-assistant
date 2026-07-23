"""StartRun unit tests with controlled dependencies (fake UoW + Clock)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from friday.application.commands import StartRunCommand
from friday.application.errors import EntityConflict, TaskNotFound
from friday.application.results import StartRunResult
from friday.application.start_run import StartRun
from friday.domain.event import RunEventType
from friday.domain.identifiers import RunEventId, RunId, TaskId
from friday.domain.run import RunStatus
from friday.domain.task import Task, TaskStatus
from tests.application.fakes import (
    T0,
    CountingUnitOfWorkFactory,
    FakeClock,
    FakeUnitOfWork,
)


def _pending_task(uow: FakeUnitOfWork) -> Task:
    task = Task.new(id=TaskId.new(), title="t", description="", created_at=T0)
    uow.task_repo.add(task)
    return task


def test_missing_task_raises_task_not_found_and_stages_nothing(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    missing_id = TaskId.new()
    with pytest.raises(TaskNotFound) as exc_info:
        StartRun(uow_factory, clock).execute(StartRunCommand(task_id=missing_id))

    assert exc_info.value.task_id == missing_id
    assert fake_uow.run_repo.items == {}
    assert fake_uow.event_store.appended == []
    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 1


@pytest.mark.parametrize("terminal", ["complete", "cancel"])
def test_terminal_task_state_is_rejected_without_partial_staging(
    fake_uow: FakeUnitOfWork,
    uow_factory: CountingUnitOfWorkFactory,
    clock: FakeClock,
    terminal: str,
) -> None:
    task = _pending_task(fake_uow)
    if terminal == "complete":
        task.start(T0)
        task.complete(T0 + timedelta(minutes=1))
    else:
        task.cancel(T0 + timedelta(minutes=1))

    with pytest.raises(EntityConflict):
        StartRun(uow_factory, clock).execute(StartRunCommand(task_id=task.id))

    assert fake_uow.run_repo.items == {}
    assert fake_uow.event_store.appended == []
    assert fake_uow.commit_count == 0


def test_pending_task_is_activated_through_its_domain_transition(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    task = _pending_task(fake_uow)

    StartRun(uow_factory, clock).execute(StartRunCommand(task_id=task.id))

    assert task.status is TaskStatus.ACTIVE
    assert task.started_at == clock.fixed_now
    assert fake_uow.task_repo.saved == [task.id]


def test_active_task_owns_the_new_run_without_a_second_activation(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    task = _pending_task(fake_uow)
    task.start(T0)

    result = StartRun(uow_factory, clock).execute(StartRunCommand(task_id=task.id))

    assert task.status is TaskStatus.ACTIVE
    assert fake_uow.task_repo.saved == []
    assert result.run_id in fake_uow.run_repo.items


def test_run_is_created_in_canonical_queued_state(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    task = _pending_task(fake_uow)

    result = StartRun(uow_factory, clock).execute(StartRunCommand(task_id=task.id))

    run = fake_uow.run_repo.get(result.run_id)
    assert run is not None
    assert isinstance(run.id, RunId)
    assert run.status is RunStatus.QUEUED
    assert run.task_id == task.id
    assert run.created_at == clock.fixed_now
    assert run.started_at is None


def test_canonical_run_created_event_is_allocated_by_orchestration(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    task = _pending_task(fake_uow)

    result = StartRun(uow_factory, clock).execute(StartRunCommand(task_id=task.id))

    events = fake_uow.event_store.list_for_run(result.run_id)
    assert [event.type for event in events] == [RunEventType.RUN_CREATED]
    event = events[0]
    assert isinstance(event.id, RunEventId)
    assert event.run_id == result.run_id
    assert event.sequence == 1
    assert event.occurred_at == clock.fixed_now
    assert event.occurred_at.tzinfo is not None
    assert event.occurred_at.utcoffset() == timedelta(0)
    assert event.payload == {"task_id": str(task.id)}
    assert event.step_id is None


def test_commits_exactly_once_on_success(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    task = _pending_task(fake_uow)
    StartRun(uow_factory, clock).execute(StartRunCommand(task_id=task.id))
    assert fake_uow.commit_count == 1
    assert fake_uow.rollback_count == 0
    assert fake_uow.closed


def test_successive_runs_get_distinct_ids_and_per_run_sequences(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory
) -> None:
    task = _pending_task(fake_uow)
    use_case = StartRun(uow_factory, FakeClock(datetime(2026, 1, 2, 4, tzinfo=UTC)))

    first = use_case.execute(StartRunCommand(task_id=task.id))
    second = use_case.execute(StartRunCommand(task_id=task.id))

    assert first.run_id != second.run_id
    listed_ids = {run.id for run in fake_uow.run_repo.list_for_task(task.id)}
    assert listed_ids == {first.run_id, second.run_id}
    for result in (first, second):
        sequences = [e.sequence for e in fake_uow.event_store.list_for_run(result.run_id)]
        assert sequences == [1]


def test_result_exposes_typed_identifiers_only(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    task = _pending_task(fake_uow)
    result = StartRun(uow_factory, clock).execute(StartRunCommand(task_id=task.id))
    assert isinstance(result, StartRunResult)
    assert isinstance(result.task_id, TaskId)
    assert isinstance(result.run_id, RunId)
