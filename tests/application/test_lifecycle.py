from __future__ import annotations

from datetime import timedelta

import pytest

from friday.application.commands import (
    CancelRunCommand,
    CancelStepCommand,
    CancelTaskCommand,
    CompleteRunCommand,
    CompleteStepCommand,
    CompleteTaskCommand,
    CreateOrderedStepCommand,
    FailRunCommand,
    FailStepCommand,
    FailTaskCommand,
    RetryFailedRunCommand,
    SkipPendingStepCommand,
    StartQueuedRunCommand,
    StartStepCommand,
)
from friday.application.errors import EntityConflict, RunNotFound, RunStepNotFound, TaskNotFound
from friday.application.lifecycle import (
    CancelRun,
    CancelStep,
    CancelTask,
    CompleteRun,
    CompleteStep,
    CompleteTask,
    CreateOrderedStep,
    FailRun,
    FailStep,
    FailTask,
    GetRun,
    GetTask,
    ListRunsForTask,
    ListTasks,
    RetryFailedRun,
    SkipPendingStep,
    StartQueuedRun,
    StartStep,
)
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import ApprovalRequestId, RunId, RunStepId, TaskId, ToolInvocationId
from friday.domain.run import Run, RunStatus
from friday.domain.step import RunStep, RunStepStatus
from friday.domain.task import Task, TaskStatus
from friday.domain.tool import ToolInvocation, ToolInvocationStatus
from tests.application.fakes import T0, CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork

FAILURE = Failure("x", "failed", True, FailureCause.RUNTIME)


def _prepared() -> tuple[FakeUnitOfWork, CountingUnitOfWorkFactory, Task, Run]:
    uow = FakeUnitOfWork()
    factory = CountingUnitOfWorkFactory(uow)
    task = Task.new(id=TaskId.new(), title="t", description="", created_at=T0)
    task.start(T0)
    uow.task_repo.add(task)
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
    uow.run_repo.add(run)
    return uow, factory, task, run


def test_read_and_success_lifecycle() -> None:
    uow, factory, task, run = _prepared()
    clock = FakeClock(T0 + timedelta(minutes=1))
    assert GetTask(factory, clock).execute(task.id).task_id == task.id
    assert ListTasks(factory, clock).execute()[0].task_id == task.id
    assert GetRun(factory, clock).execute(run.id).run_id == run.id
    assert ListRunsForTask(factory, clock).execute(task.id)[0].run_id == run.id
    StartQueuedRun(factory, clock).execute(StartQueuedRunCommand(run.id))
    step = CreateOrderedStep(factory, clock).execute(CreateOrderedStepCommand(run.id, "s"))
    StartStep(factory, clock).execute(StartStepCommand(step.step_id))
    CompleteStep(factory, clock).execute(CompleteStepCommand(step.step_id))
    CompleteRun(factory, clock).execute(CompleteRunCommand(run.id))
    CompleteTask(factory, clock).execute(CompleteTaskCommand(task.id))
    assert [e.type.value for e in uow.event_store.appended] == [
        "run_started",
        "step_created",
        "step_started",
        "step_succeeded",
        "run_succeeded",
    ]
    assert uow.task_event_store.appended[-1].type.value == "task_completed"


def test_failure_retry_skip_and_cancellation_propagate() -> None:
    uow, factory, task, run = _prepared()
    clock = FakeClock(T0 + timedelta(minutes=1))
    StartQueuedRun(factory, clock).execute(StartQueuedRunCommand(run.id))
    first = CreateOrderedStep(factory, clock).execute(CreateOrderedStepCommand(run.id, "first"))
    second = CreateOrderedStep(factory, clock).execute(CreateOrderedStepCommand(run.id, "second"))
    StartStep(factory, clock).execute(StartStepCommand(first.step_id))
    tool = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=run.id,
        step_id=first.step_id,
        tool_name="tool",
        requested_input=None,
        requested_at=T0,
    )
    uow.tool_repo.add(tool)
    FailStep(factory, clock).execute(FailStepCommand(first.step_id, FAILURE))
    assert tool.status is ToolInvocationStatus.CANCELLED
    SkipPendingStep(factory, clock).execute(SkipPendingStepCommand(second.step_id))
    FailRun(factory, clock).execute(FailRunCommand(run.id, FAILURE))
    retry = RetryFailedRun(factory, clock).execute(RetryFailedRunCommand(run.id))
    assert retry.run_id != run.id
    CancelRun(factory, clock).execute(CancelRunCommand(retry.run_id))
    CancelTask(factory, clock).execute(CancelTaskCommand(task.id))


def test_terminal_replays_are_idempotent_and_conflicts_are_stable() -> None:
    uow, factory, task, run = _prepared()
    clock = FakeClock(T0)
    CancelRun(factory, clock).execute(CancelRunCommand(run.id))
    events = len(uow.event_store.appended)
    CancelRun(factory, clock).execute(CancelRunCommand(run.id))
    assert len(uow.event_store.appended) == events
    CancelTask(factory, clock).execute(CancelTaskCommand(task.id))
    task_events = len(uow.task_event_store.appended)
    CancelTask(factory, clock).execute(CancelTaskCommand(task.id))
    assert len(uow.task_event_store.appended) == task_events
    with pytest.raises(EntityConflict):
        CompleteTask(factory, clock).execute(CompleteTaskCommand(task.id))
    with pytest.raises(TaskNotFound):
        GetTask(factory, clock).execute(TaskId.new())
    with pytest.raises(RunNotFound):
        GetRun(factory, clock).execute(RunId.new())


def test_explicit_task_failure_is_immutable() -> None:
    uow, factory, task, _ = _prepared()
    clock = FakeClock(T0)
    FailTask(factory, clock).execute(FailTaskCommand(task.id, FAILURE))
    FailTask(factory, clock).execute(FailTaskCommand(task.id, FAILURE))
    with pytest.raises(EntityConflict):
        FailTask(factory, clock).execute(
            FailTaskCommand(task.id, Failure("y", "different", False, FailureCause.INTERNAL))
        )


def _task_at(status: TaskStatus) -> Task:
    task = Task.new(id=TaskId.new(), title="t", description="", created_at=T0)
    if status is not TaskStatus.PENDING:
        task.start(T0)
    if status is TaskStatus.COMPLETED:
        task.complete(T0)
    elif status is TaskStatus.FAILED:
        task.fail(T0, FAILURE)
    elif status is TaskStatus.CANCELLED:
        task.cancel(T0)
    return task


def _run_at(task_id: TaskId, status: RunStatus) -> Run:
    run = Run.new(id=RunId.new(), task_id=task_id, created_at=T0)
    if status is not RunStatus.QUEUED:
        run.start(T0)
    if status is RunStatus.WAITING_FOR_APPROVAL:
        from friday.domain.identifiers import ApprovalRequestId

        run.wait_for_approval(T0, ApprovalRequestId.new())
    elif status is RunStatus.SUCCEEDED:
        run.succeed(T0)
    elif status is RunStatus.FAILED:
        run.fail(T0, FAILURE)
    elif status is RunStatus.CANCELLED:
        run.cancel(T0)
    return run


def _step_at(run_id: RunId, status: RunStepStatus) -> RunStep:
    step = RunStep.new(
        id=RunStepId.new(),
        run_id=run_id,
        name="s",
        position=0,
        created_at=T0,
    )
    if status is not RunStepStatus.PENDING:
        step.start(T0)
    if status is RunStepStatus.SUCCEEDED:
        step.succeed(T0)
    elif status is RunStepStatus.FAILED:
        step.fail(T0, FAILURE)
    elif status is RunStepStatus.WAITING_FOR_APPROVAL:
        step.wait_for_approval(T0, ApprovalRequestId.new())
    elif status is RunStepStatus.SKIPPED:
        step = RunStep.new(id=step.id, run_id=run_id, name="s", position=0, created_at=T0)
        step.skip(T0)
    elif status is RunStepStatus.CANCELLED:
        step.cancel(T0)
    return step


@pytest.mark.parametrize("status", list(TaskStatus), ids=lambda value: value.value)
def test_task_terminal_commands_cover_every_source_state(status: TaskStatus) -> None:
    uow = FakeUnitOfWork()
    factory = CountingUnitOfWorkFactory(uow)
    task = _task_at(status)
    uow.task_repo.add(task)
    command = CompleteTaskCommand(task.id)
    if status is TaskStatus.ACTIVE:
        CompleteTask(factory, FakeClock()).execute(command)
        assert task.status is TaskStatus.COMPLETED
    elif status is TaskStatus.COMPLETED:
        CompleteTask(factory, FakeClock()).execute(command)
    else:
        with pytest.raises(EntityConflict):
            CompleteTask(factory, FakeClock()).execute(command)


@pytest.mark.parametrize("status", list(RunStatus), ids=lambda value: value.value)
def test_start_queued_run_covers_every_source_state(status: RunStatus) -> None:
    uow, factory, task, _ = _prepared()
    run = _run_at(task.id, status)
    uow.run_repo.items = {run.id: run}
    if status in {RunStatus.QUEUED, RunStatus.RUNNING}:
        StartQueuedRun(factory, FakeClock()).execute(StartQueuedRunCommand(run.id))
    else:
        with pytest.raises(EntityConflict):
            StartQueuedRun(factory, FakeClock()).execute(StartQueuedRunCommand(run.id))


@pytest.mark.parametrize("status", list(RunStepStatus), ids=lambda value: value.value)
def test_step_commands_cover_every_source_state(status: RunStepStatus) -> None:
    uow, factory, task, run = _prepared()
    run.start(T0)
    uow.run_repo.save(run)
    step = _step_at(run.id, status)
    uow.step_repo.add(step)
    if status in {RunStepStatus.PENDING, RunStepStatus.RUNNING}:
        StartStep(factory, FakeClock()).execute(StartStepCommand(step.id))
    else:
        with pytest.raises(EntityConflict):
            StartStep(factory, FakeClock()).execute(StartStepCommand(step.id))


@pytest.mark.parametrize("kind", ["start", "complete", "fail", "cancel", "retry", "create-step"])
def test_run_commands_report_missing_run(kind: str) -> None:
    factory = CountingUnitOfWorkFactory(FakeUnitOfWork())
    run_id = RunId.new()
    with pytest.raises(RunNotFound):
        if kind == "start":
            StartQueuedRun(factory, FakeClock()).execute(StartQueuedRunCommand(run_id))
        elif kind == "complete":
            CompleteRun(factory, FakeClock()).execute(CompleteRunCommand(run_id))
        elif kind == "fail":
            FailRun(factory, FakeClock()).execute(FailRunCommand(run_id, FAILURE))
        elif kind == "cancel":
            CancelRun(factory, FakeClock()).execute(CancelRunCommand(run_id))
        elif kind == "retry":
            RetryFailedRun(factory, FakeClock()).execute(RetryFailedRunCommand(run_id))
        else:
            CreateOrderedStep(factory, FakeClock()).execute(CreateOrderedStepCommand(run_id, "s"))


@pytest.mark.parametrize("kind", ["start", "complete", "fail", "skip", "cancel"])
def test_step_commands_report_missing_step(kind: str) -> None:
    factory = CountingUnitOfWorkFactory(FakeUnitOfWork())
    step_id = RunStepId.new()
    with pytest.raises(RunStepNotFound):
        if kind == "start":
            StartStep(factory, FakeClock()).execute(StartStepCommand(step_id))
        elif kind == "complete":
            CompleteStep(factory, FakeClock()).execute(CompleteStepCommand(step_id))
        elif kind == "fail":
            FailStep(factory, FakeClock()).execute(FailStepCommand(step_id, FAILURE))
        elif kind == "skip":
            SkipPendingStep(factory, FakeClock()).execute(SkipPendingStepCommand(step_id))
        else:
            CancelStep(factory, FakeClock()).execute(CancelStepCommand(step_id))


def _cancellation_tree() -> tuple[
    FakeUnitOfWork,
    CountingUnitOfWorkFactory,
    Task,
    Run,
    RunStep,
    RunStep,
    ToolInvocation,
    ToolInvocation,
    ToolInvocation,
]:
    uow, factory, task, run = _prepared()
    run.start(T0)
    first = RunStep.new(id=RunStepId.new(), run_id=run.id, name="first", position=1, created_at=T0)
    second = RunStep.new(
        id=RunStepId.new(), run_id=run.id, name="second", position=2, created_at=T0
    )
    uow.step_repo.add(first)
    uow.step_repo.add(second)
    direct = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=run.id,
        tool_name="direct",
        requested_input=None,
        requested_at=T0,
    )
    owned = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=run.id,
        step_id=first.id,
        tool_name="owned",
        requested_input=None,
        requested_at=T0,
    )
    terminal = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=run.id,
        step_id=second.id,
        tool_name="terminal",
        requested_input=None,
        requested_at=T0,
    )
    terminal.cancel(T0)
    for tool in (direct, owned, terminal):
        uow.tool_repo.add(tool)
    return uow, factory, task, run, first, second, direct, owned, terminal


def test_cancel_task_cascades_in_stable_order_without_duplicate_replay_events() -> None:
    uow, factory, task, run, first, second, direct, owned, terminal = _cancellation_tree()
    CancelTask(factory, FakeClock()).execute(CancelTaskCommand(task.id))

    assert task.status is TaskStatus.CANCELLED
    assert run.status is RunStatus.CANCELLED
    assert [step.status for step in (first, second)] == [
        RunStepStatus.CANCELLED,
        RunStepStatus.CANCELLED,
    ]
    assert [tool.status for tool in (direct, owned, terminal)] == [
        ToolInvocationStatus.CANCELLED,
        ToolInvocationStatus.CANCELLED,
        ToolInvocationStatus.CANCELLED,
    ]
    assert [event.type.value for event in uow.event_store.appended] == [
        "run_cancelled",
        "step_cancelled",
        "tool_invocation_cancelled",
        "step_cancelled",
        "tool_invocation_cancelled",
    ]
    assert [event.sequence for event in uow.event_store.appended] == [1, 2, 3, 4, 5]
    assert [event.occurred_at for event in uow.event_store.appended] == [T0] * 5
    assert uow.task_event_store.appended[-1].type.value == "task_cancelled"
    counts = (len(uow.event_store.appended), len(uow.task_event_store.appended))
    CancelTask(factory, FakeClock()).execute(CancelTaskCommand(task.id))
    assert (len(uow.event_store.appended), len(uow.task_event_store.appended)) == counts


def test_cancel_step_does_not_cancel_other_step_or_run_owned_invocations() -> None:
    uow, factory, _, _, first, second, direct, owned, terminal = _cancellation_tree()
    CancelStep(factory, FakeClock()).execute(CancelStepCommand(first.id))

    assert first.status is RunStepStatus.CANCELLED
    assert second.status is RunStepStatus.PENDING
    assert direct.status is ToolInvocationStatus.REQUESTED
    assert owned.status is ToolInvocationStatus.CANCELLED
    assert terminal.status is ToolInvocationStatus.CANCELLED
    event_count = len(uow.event_store.appended)
    CancelStep(factory, FakeClock()).execute(CancelStepCommand(first.id))
    assert len(uow.event_store.appended) == event_count


@pytest.mark.parametrize("blocker", ["run", "step", "tool"])
def test_complete_task_rejects_each_non_terminal_descendant(blocker: str) -> None:
    uow, factory, task, run = _prepared()
    run.cancel(T0)
    if blocker == "run":
        run = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
        uow.run_repo.add(run)
    elif blocker == "step":
        uow.step_repo.add(
            RunStep.new(id=RunStepId.new(), run_id=run.id, name="s", position=0, created_at=T0)
        )
    else:
        uow.tool_repo.add(
            ToolInvocation.new(
                id=ToolInvocationId.new(),
                run_id=run.id,
                tool_name="t",
                requested_input=None,
                requested_at=T0,
            )
        )
    with pytest.raises(EntityConflict):
        CompleteTask(factory, FakeClock()).execute(CompleteTaskCommand(task.id))


def test_run_and_step_failure_replays_preserve_original_failure_and_task_retryability() -> None:
    uow, factory, task, run = _prepared()
    run.start(T0)
    step = RunStep.new(id=RunStepId.new(), run_id=run.id, name="s", position=0, created_at=T0)
    step.start(T0)
    uow.step_repo.add(step)
    FailStep(factory, FakeClock()).execute(FailStepCommand(step.id, FAILURE))
    step_events = len(uow.event_store.appended)
    FailStep(factory, FakeClock()).execute(FailStepCommand(step.id, FAILURE))
    assert len(uow.event_store.appended) == step_events
    with pytest.raises(EntityConflict):
        FailStep(factory, FakeClock()).execute(
            FailStepCommand(step.id, Failure("other", "x", False, FailureCause.INTERNAL))
        )
    FailRun(factory, FakeClock()).execute(FailRunCommand(run.id, FAILURE))
    run_events = len(uow.event_store.appended)
    FailRun(factory, FakeClock()).execute(FailRunCommand(run.id, FAILURE))
    assert len(uow.event_store.appended) == run_events
    assert task.status is TaskStatus.ACTIVE
    with pytest.raises(EntityConflict):
        FailRun(factory, FakeClock()).execute(
            FailRunCommand(run.id, Failure("other", "x", False, FailureCause.INTERNAL))
        )


def test_retry_rejects_non_latest_and_non_terminal_run_without_creating_duplicates() -> None:
    uow, factory, task, first = _prepared()
    first.start(T0)
    first.fail(T0, FAILURE)
    later = Run.new(id=RunId.new(), task_id=task.id, created_at=T0 + timedelta(seconds=1))
    later.start(T0 + timedelta(seconds=1))
    later.fail(T0 + timedelta(seconds=1), FAILURE)
    uow.run_repo.add(later)
    with pytest.raises(EntityConflict):
        RetryFailedRun(factory, FakeClock()).execute(RetryFailedRunCommand(first.id))
    active = Run.new(id=RunId.new(), task_id=task.id, created_at=T0 + timedelta(seconds=2))
    uow.run_repo.add(active)
    with pytest.raises(EntityConflict):
        RetryFailedRun(factory, FakeClock()).execute(RetryFailedRunCommand(later.id))
    assert len(uow.run_repo.list_for_task(task.id)) == 3


def test_cancel_run_cascades_without_cancelling_the_task_and_replays_once() -> None:
    uow, factory, task, run, first, second, direct, owned, terminal = _cancellation_tree()
    CancelRun(factory, FakeClock()).execute(CancelRunCommand(run.id))
    assert task.status is TaskStatus.ACTIVE
    assert run.status is RunStatus.CANCELLED
    assert [step.status for step in (first, second)] == [RunStepStatus.CANCELLED] * 2
    assert [tool.status for tool in (direct, owned, terminal)] == [
        ToolInvocationStatus.CANCELLED
    ] * 3
    assert [event.type.value for event in uow.event_store.appended] == [
        "run_cancelled",
        "step_cancelled",
        "tool_invocation_cancelled",
        "step_cancelled",
        "tool_invocation_cancelled",
    ]
    events = len(uow.event_store.appended)
    CancelRun(factory, FakeClock()).execute(CancelRunCommand(run.id))
    assert len(uow.event_store.appended) == events


def test_list_runs_distinguishes_missing_task_from_empty_task() -> None:
    uow, factory, task, _ = _prepared()
    uow.run_repo.items.clear()
    assert ListRunsForTask(factory, FakeClock()).execute(task.id) == []
    with pytest.raises(TaskNotFound):
        ListRunsForTask(factory, FakeClock()).execute(TaskId.new())


def test_start_queued_run_requires_existing_active_task() -> None:
    uow = FakeUnitOfWork()
    factory = CountingUnitOfWorkFactory(uow)
    orphan = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)
    uow.run_repo.add(orphan)
    with pytest.raises(TaskNotFound):
        StartQueuedRun(factory, FakeClock()).execute(StartQueuedRunCommand(orphan.id))
    task = _task_at(TaskStatus.PENDING)
    uow.task_repo.add(task)
    blocked = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
    uow.run_repo.add(blocked)
    with pytest.raises(EntityConflict):
        StartQueuedRun(factory, FakeClock()).execute(StartQueuedRunCommand(blocked.id))


def test_run_terminal_replays_and_retry_parent_preconditions_are_stable() -> None:
    uow, factory, task, run = _prepared()
    run.start(T0)
    run.succeed(T0)
    CompleteRun(factory, FakeClock()).execute(CompleteRunCommand(run.id))
    with pytest.raises(EntityConflict):
        CancelRun(factory, FakeClock()).execute(CancelRunCommand(run.id))
    failed = Run.new(id=RunId.new(), task_id=task.id, created_at=T0 + timedelta(seconds=1))
    failed.start(T0 + timedelta(seconds=1))
    failed.fail(T0 + timedelta(seconds=1), FAILURE)
    uow.run_repo.add(failed)
    task.cancel(T0)
    with pytest.raises(EntityConflict):
        RetryFailedRun(factory, FakeClock()).execute(RetryFailedRunCommand(failed.id))


@pytest.mark.parametrize(
    "status", [RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.WAITING_FOR_APPROVAL]
)
def test_complete_run_rejects_non_terminal_or_invalid_run_states(status: RunStatus) -> None:
    uow, factory, task, _ = _prepared()
    run = _run_at(task.id, status)
    uow.run_repo.items = {run.id: run}
    if status is RunStatus.RUNNING:
        CompleteRun(factory, FakeClock()).execute(CompleteRunCommand(run.id))
        assert run.status is RunStatus.SUCCEEDED
    else:
        with pytest.raises(EntityConflict):
            CompleteRun(factory, FakeClock()).execute(CompleteRunCommand(run.id))


@pytest.mark.parametrize("child", ["step", "tool"])
def test_complete_run_rejects_non_terminal_descendants(child: str) -> None:
    uow, factory, _, run = _prepared()
    run.start(T0)
    if child == "step":
        uow.step_repo.add(
            RunStep.new(id=RunStepId.new(), run_id=run.id, name="s", position=0, created_at=T0)
        )
    else:
        uow.tool_repo.add(
            ToolInvocation.new(
                id=ToolInvocationId.new(),
                run_id=run.id,
                tool_name="t",
                requested_input=None,
                requested_at=T0,
            )
        )
    with pytest.raises(EntityConflict):
        CompleteRun(factory, FakeClock()).execute(CompleteRunCommand(run.id))


@pytest.mark.parametrize("status", list(RunStatus), ids=lambda value: value.value)
def test_fail_run_accepts_only_running_and_never_revives_terminal(status: RunStatus) -> None:
    uow, factory, task, _ = _prepared()
    run = _run_at(task.id, status)
    uow.run_repo.items = {run.id: run}
    if status is RunStatus.RUNNING:
        FailRun(factory, FakeClock()).execute(FailRunCommand(run.id, FAILURE))
        assert run.status is RunStatus.FAILED
    elif status is RunStatus.FAILED:
        FailRun(factory, FakeClock()).execute(FailRunCommand(run.id, FAILURE))
    else:
        with pytest.raises(EntityConflict):
            FailRun(factory, FakeClock()).execute(FailRunCommand(run.id, FAILURE))


@pytest.mark.parametrize("status", [RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED])
def test_create_ordered_step_rejects_terminal_run(status: RunStatus) -> None:
    uow, factory, task, _ = _prepared()
    run = _run_at(task.id, status)
    uow.run_repo.items = {run.id: run}
    with pytest.raises(EntityConflict):
        CreateOrderedStep(factory, FakeClock()).execute(CreateOrderedStepCommand(run.id, "s"))


def test_create_ordered_step_allocates_scoped_consecutive_positions() -> None:
    uow, factory, _, run = _prepared()
    first = CreateOrderedStep(factory, FakeClock()).execute(
        CreateOrderedStepCommand(run.id, "first")
    )
    second = CreateOrderedStep(factory, FakeClock()).execute(
        CreateOrderedStepCommand(run.id, "second")
    )
    assert (first.position, second.position) == (0, 1)


@pytest.mark.parametrize("status", list(RunStepStatus), ids=lambda value: value.value)
def test_complete_skip_and_cancel_step_terminal_policy(status: RunStepStatus) -> None:
    uow, factory, task, run = _prepared()
    run.start(T0)
    step = _step_at(run.id, status)
    uow.step_repo.add(step)
    complete = CompleteStep(factory, FakeClock())
    skip = SkipPendingStep(factory, FakeClock())
    cancel = CancelStep(factory, FakeClock())
    if status is RunStepStatus.RUNNING or status is RunStepStatus.SUCCEEDED:
        complete.execute(CompleteStepCommand(step.id))
    else:
        with pytest.raises(EntityConflict):
            complete.execute(CompleteStepCommand(step.id))
    if status is RunStepStatus.PENDING or status is RunStepStatus.SKIPPED:
        skip.execute(SkipPendingStepCommand(step.id))
    else:
        with pytest.raises(EntityConflict):
            skip.execute(SkipPendingStepCommand(step.id))
    if status in {RunStepStatus.CANCELLED, RunStepStatus.WAITING_FOR_APPROVAL}:
        cancel.execute(CancelStepCommand(step.id))
    else:
        with pytest.raises(EntityConflict):
            cancel.execute(CancelStepCommand(step.id))
