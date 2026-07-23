"""CreateTask/StartRun integration against the real SQLAlchemy Unit of Work.

Fault-injection tests sabotage one boundary at a time (run staging, sequence
allocation, event append, flush, commit) and then open a NEW session to prove
that no partial Task/Run/RunEvent state became durable. StartRun currently
appends exactly one canonical event (`run_created`), so the "during one of
multiple event appends" boundary from the phase plan collapses into the
sequence-allocation and append boundaries below."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from friday.application.commands import (
    CancelTaskCommand,
    CreateOrderedStepCommand,
    CreateTaskCommand,
    StartQueuedRunCommand,
    StartRunCommand,
)
from friday.application.create_task import CreateTask
from friday.application.errors import TransactionFailure
from friday.application.lifecycle import CancelTask, CreateOrderedStep, StartQueuedRun
from friday.application.start_run import StartRun
from friday.domain.event import RunEvent, RunEventType
from friday.domain.identifiers import RunId, TaskId, ToolInvocationId
from friday.domain.run import Run, RunStatus
from friday.domain.task import TaskStatus
from friday.domain.tool import ToolInvocation, ToolInvocationStatus
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.models import Base
from friday.infrastructure.persistence.unit_of_work import (
    SqlAlchemyUnitOfWork,
    create_unit_of_work_factory,
)
from tests.application.fakes import FakeClock

T0 = datetime(2026, 1, 2, 3, tzinfo=UTC)
T1 = T0 + timedelta(minutes=1)


@pytest.fixture
def session_factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


def _create_pending_task(session_factory: sessionmaker[Session]) -> TaskId:
    factory = create_unit_of_work_factory(session_factory)
    result = CreateTask(factory, FakeClock(T0)).execute(
        CreateTaskCommand(title="integration", description="d")
    )
    return result.task_id


def _fresh_state(
    session_factory: sessionmaker[Session], task_id: TaskId
) -> tuple[TaskStatus | None, list[Run], list[RunEvent]]:
    with SqlAlchemyUnitOfWork(session_factory()) as uow:
        task = uow.tasks.get(task_id)
        runs = uow.runs.list_for_task(task_id)
        events = [e for run in runs for e in uow.events.list_for_run(run.id)]
        return (task.status if task else None, runs, events)


def test_created_task_survives_a_new_session_read(
    session_factory: sessionmaker[Session],
) -> None:
    task_id = _create_pending_task(session_factory)

    with SqlAlchemyUnitOfWork(session_factory()) as uow:
        task = uow.tasks.get(task_id)
    assert task is not None
    assert task.status is TaskStatus.PENDING
    assert task.created_at == T0


def test_start_run_atomically_persists_task_run_and_event(
    session_factory: sessionmaker[Session],
) -> None:
    task_id = _create_pending_task(session_factory)
    factory = create_unit_of_work_factory(session_factory)

    result = StartRun(factory, FakeClock(T1)).execute(StartRunCommand(task_id=task_id))

    status, runs, events = _fresh_state(session_factory, task_id)
    assert status is TaskStatus.ACTIVE
    assert [run.id for run in runs] == [result.run_id]
    assert runs[0].status is RunStatus.QUEUED
    assert [(e.type, e.sequence) for e in events] == [(RunEventType.RUN_CREATED, 1)]
    assert events[0].occurred_at == T1
    assert events[0].occurred_at.utcoffset() == timedelta(0)


def test_successive_start_runs_accumulate_ordered_attempts(
    session_factory: sessionmaker[Session],
) -> None:
    task_id = _create_pending_task(session_factory)
    factory = create_unit_of_work_factory(session_factory)

    first = StartRun(factory, FakeClock(T1)).execute(StartRunCommand(task_id=task_id))
    second = StartRun(factory, FakeClock(T1 + timedelta(minutes=1))).execute(
        StartRunCommand(task_id=task_id)
    )

    _, runs, events = _fresh_state(session_factory, task_id)
    assert [run.id for run in runs] == [first.run_id, second.run_id]
    assert [e.sequence for e in events] == [1, 1]  # per-run sequences


def _sabotaged_start_run(
    session_factory: sessionmaker[Session],
    sabotage: Callable[[SqlAlchemyUnitOfWork], None],
    task_id: TaskId,
) -> pytest.ExceptionInfo[Exception]:
    uow = SqlAlchemyUnitOfWork(session_factory())
    sabotage(uow)
    with pytest.raises(Exception) as exc_info:
        StartRun(lambda: uow, FakeClock(T1)).execute(StartRunCommand(task_id=task_id))
    return exc_info


def _assert_nothing_became_durable(session_factory: sessionmaker[Session], task_id: TaskId) -> None:
    status, runs, events = _fresh_state(session_factory, task_id)
    assert status is TaskStatus.PENDING  # activation rolled back
    assert runs == []
    assert events == []


def test_failure_after_task_mutation_before_run_staging_rolls_back_all(
    session_factory: sessionmaker[Session],
) -> None:
    task_id = _create_pending_task(session_factory)

    def sabotage(uow: SqlAlchemyUnitOfWork) -> None:
        def failing_add(run: Run) -> None:
            raise RuntimeError("run staging failed")

        uow.runs.add = failing_add  # type: ignore[method-assign]

    _sabotaged_start_run(session_factory, sabotage, task_id)
    _assert_nothing_became_durable(session_factory, task_id)


def test_failure_during_sequence_allocation_rolls_back_all(
    session_factory: sessionmaker[Session],
) -> None:
    task_id = _create_pending_task(session_factory)

    def sabotage(uow: SqlAlchemyUnitOfWork) -> None:
        def failing_next_sequence(run_id: RunId) -> int:
            raise RuntimeError("sequence allocation failed")

        uow.events.next_sequence = failing_next_sequence  # type: ignore[method-assign]

    _sabotaged_start_run(session_factory, sabotage, task_id)
    _assert_nothing_became_durable(session_factory, task_id)


def test_failure_during_event_append_rolls_back_all(
    session_factory: sessionmaker[Session],
) -> None:
    task_id = _create_pending_task(session_factory)

    def sabotage(uow: SqlAlchemyUnitOfWork) -> None:
        def failing_append(event: RunEvent) -> None:
            raise RuntimeError("event append failed")

        uow.events.append = failing_append  # type: ignore[method-assign]

    _sabotaged_start_run(session_factory, sabotage, task_id)
    _assert_nothing_became_durable(session_factory, task_id)


def test_failure_during_flush_rolls_back_all(
    session_factory: sessionmaker[Session],
) -> None:
    task_id = _create_pending_task(session_factory)

    def sabotage(uow: SqlAlchemyUnitOfWork) -> None:
        def failing_flush(objects: object = None) -> None:
            raise OperationalError("flush", {}, Exception("disk full"))

        uow._session.flush = failing_flush  # type: ignore[method-assign]

    exc_info = _sabotaged_start_run(session_factory, sabotage, task_id)
    assert isinstance(exc_info.value, TransactionFailure)
    _assert_nothing_became_durable(session_factory, task_id)


def test_failure_during_commit_rolls_back_all(
    session_factory: sessionmaker[Session],
) -> None:
    task_id = _create_pending_task(session_factory)

    def sabotage(uow: SqlAlchemyUnitOfWork) -> None:
        def failing_commit() -> None:
            raise OperationalError("commit", {}, Exception("disk full"))

        uow._session.commit = failing_commit  # type: ignore[method-assign]

    exc_info = _sabotaged_start_run(session_factory, sabotage, task_id)
    assert isinstance(exc_info.value, TransactionFailure)
    _assert_nothing_became_durable(session_factory, task_id)


def test_cancel_task_event_append_failure_rolls_back_the_entire_persisted_hierarchy(
    session_factory: sessionmaker[Session],
) -> None:
    """A fresh session sees no partial Task/Run/Step/Tool mutation after failure."""
    task_id = _create_pending_task(session_factory)
    factory = create_unit_of_work_factory(session_factory)
    run_id = StartRun(factory, FakeClock(T0)).execute(StartRunCommand(task_id)).run_id
    StartQueuedRun(factory, FakeClock(T1)).execute(StartQueuedRunCommand(run_id))
    step_id = (
        CreateOrderedStep(factory, FakeClock(T1))
        .execute(CreateOrderedStepCommand(run_id, "ordered"))
        .step_id
    )
    with SqlAlchemyUnitOfWork(session_factory()) as uow:
        uow.tool_invocations.add(
            ToolInvocation.new(
                id=ToolInvocationId.new(),
                run_id=run_id,
                step_id=step_id,
                tool_name="metadata-only",
                requested_input=None,
                requested_at=T1,
            )
        )
        uow.commit()

    sabotaged = SqlAlchemyUnitOfWork(session_factory())

    def failing_append(event: RunEvent) -> None:
        raise RuntimeError("event append failed")

    sabotaged.events.append = failing_append  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        CancelTask(lambda: sabotaged, FakeClock(T1)).execute(CancelTaskCommand(task_id))

    with SqlAlchemyUnitOfWork(session_factory()) as fresh:
        task = fresh.tasks.get(task_id)
        run = fresh.runs.get(run_id)
        step = fresh.steps.get(step_id)
        tools = fresh.tool_invocations.list_for_step(step_id)
        assert task is not None and task.status is TaskStatus.ACTIVE
        assert run is not None and run.status is RunStatus.RUNNING
        assert step is not None and step.status.value == "pending"
        assert [tool.status for tool in tools] == [ToolInvocationStatus.REQUESTED]
