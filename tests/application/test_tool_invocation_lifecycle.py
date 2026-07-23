"""ToolInvocation lifecycle: metadata-only transitions, approval
authorization references, replay/conflict matrix, deterministic events."""

from __future__ import annotations

from datetime import timedelta

import pytest

from friday.application.commands import (
    CancelToolInvocationCommand,
    MarkToolInvocationFailedCommand,
    MarkToolInvocationRunningCommand,
    MarkToolInvocationSucceededCommand,
    RequestToolInvocationCommand,
)
from friday.application.errors import (
    EntityConflict,
    RunNotFound,
    RunStepNotFound,
    ToolInvocationNotFound,
)
from friday.application.tool_invocation_lifecycle import (
    CancelToolInvocation,
    GetToolInvocation,
    ListToolInvocationsForRun,
    ListToolInvocationsForStep,
    MarkToolInvocationFailed,
    MarkToolInvocationRunning,
    MarkToolInvocationSucceeded,
    RequestToolInvocation,
)
from friday.domain.approval import ApprovalCategory, ApprovalRequest
from friday.domain.errors import DomainValidationError
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import (
    ApprovalRequestId,
    RunId,
    RunStepId,
    TaskId,
    ToolInvocationId,
)
from friday.domain.run import Run
from friday.domain.step import RunStep
from friday.domain.task import Task
from friday.domain.tool import ToolInvocationStatus
from tests.application.fakes import T0, CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork

T1 = T0 + timedelta(minutes=1)
FAILURE = Failure("tool_error", "boom", True, FailureCause.TOOL)


def _prepared(
    *, with_step: bool = False
) -> tuple[FakeUnitOfWork, CountingUnitOfWorkFactory, Run, RunStep | None]:
    uow = FakeUnitOfWork()
    factory = CountingUnitOfWorkFactory(uow)
    task = Task.new(id=TaskId.new(), title="t", description="", created_at=T0)
    task.start(T0)
    uow.task_repo.add(task)
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
    run.start(T0)
    uow.run_repo.add(run)
    step: RunStep | None = None
    if with_step:
        step = RunStep.new(id=RunStepId.new(), run_id=run.id, name="s", position=0, created_at=T0)
        step.start(T0)
        uow.step_repo.add(step)
    return uow, factory, run, step


def _approved_approval(
    uow: FakeUnitOfWork, run: Run, step: RunStep | None = None
) -> ApprovalRequest:
    approval = ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=run.id,
        category=ApprovalCategory.TOOL_EXECUTION,
        summary="s",
        reason="r",
        requested_action="a",
        requested_input=None,
        requested_at=T0,
        step_id=step.id if step else None,
    )
    approval.approve(T0, resolver="alice")
    uow.approval_repo.add(approval)
    return approval


def _request(
    run: Run,
    step: RunStep | None = None,
    approval_id: ApprovalRequestId | None = None,
) -> RequestToolInvocationCommand:
    return RequestToolInvocationCommand(
        run_id=run.id,
        tool_name="shell",
        requested_input={"cmd": "ls"},
        step_id=step.id if step else None,
        approval_request_id=approval_id,
    )


def test_request_run_owned_invocation_appends_event() -> None:
    uow, factory, run, _ = _prepared()
    result = RequestToolInvocation(factory, FakeClock(T1)).execute(_request(run))
    assert result.status is ToolInvocationStatus.REQUESTED
    assert result.approval_request_id is None
    event = uow.event_store.appended[-1]
    assert event.type.value == "tool_invocation_requested"
    assert event.payload == {
        "tool_invocation_id": str(result.invocation_id),
        "tool_name": "shell",
        "approval_request_id": None,
    }
    assert uow.commit_count == 1


def test_request_step_owned_invocation_with_approved_reference() -> None:
    uow, factory, run, step = _prepared(with_step=True)
    assert step is not None
    approval = _approved_approval(uow, run, step)
    result = RequestToolInvocation(factory, FakeClock(T1)).execute(_request(run, step, approval.id))
    assert result.step_id == step.id
    assert result.approval_request_id == approval.id
    assert uow.event_store.appended[-1].step_id == step.id


def test_request_rejects_missing_and_non_running_owners() -> None:
    uow, factory, run, _ = _prepared()
    with pytest.raises(RunNotFound):
        RequestToolInvocation(factory, FakeClock(T1)).execute(
            RequestToolInvocationCommand(RunId.new(), "shell", None)
        )
    with pytest.raises(RunStepNotFound):
        RequestToolInvocation(factory, FakeClock(T1)).execute(
            RequestToolInvocationCommand(run.id, "shell", None, step_id=RunStepId.new())
        )
    run.succeed(T0)
    with pytest.raises(EntityConflict):
        RequestToolInvocation(factory, FakeClock(T1)).execute(_request(run))


def test_request_rejects_step_of_another_run() -> None:
    uow, factory, run, _ = _prepared()
    foreign = RunStep.new(
        id=RunStepId.new(), run_id=RunId.new(), name="s", position=0, created_at=T0
    )
    foreign.start(T0)
    uow.step_repo.add(foreign)
    with pytest.raises(EntityConflict):
        RequestToolInvocation(factory, FakeClock(T1)).execute(
            RequestToolInvocationCommand(run.id, "shell", None, step_id=foreign.id)
        )


def test_request_rejects_invalid_approval_references() -> None:
    uow, factory, run, _ = _prepared()
    with pytest.raises(EntityConflict):
        RequestToolInvocation(factory, FakeClock(T1)).execute(
            _request(run, approval_id=ApprovalRequestId.new())
        )
    # approval owned by an unrelated run
    other_task = Task.new(id=TaskId.new(), title="t", description="", created_at=T0)
    other_task.start(T0)
    uow.task_repo.add(other_task)
    other_run = Run.new(id=RunId.new(), task_id=other_task.id, created_at=T0)
    other_run.start(T0)
    uow.run_repo.add(other_run)
    foreign_approval = _approved_approval(uow, other_run)
    with pytest.raises(EntityConflict):
        RequestToolInvocation(factory, FakeClock(T1)).execute(
            _request(run, approval_id=foreign_approval.id)
        )


def test_request_rejects_pending_approval_reference() -> None:
    uow, factory, run, _ = _prepared()
    pending = ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=run.id,
        category=ApprovalCategory.TOOL_EXECUTION,
        summary="s",
        reason="r",
        requested_action="a",
        requested_input=None,
        requested_at=T0,
    )
    uow.approval_repo.add(pending)
    with pytest.raises(EntityConflict):
        RequestToolInvocation(factory, FakeClock(T1)).execute(_request(run, approval_id=pending.id))


def test_request_rejects_approval_scoped_to_different_step() -> None:
    uow, factory, run, step = _prepared(with_step=True)
    assert step is not None
    approval = _approved_approval(uow, run, step)
    with pytest.raises(EntityConflict):
        RequestToolInvocation(factory, FakeClock(T1)).execute(
            _request(run, step=None, approval_id=approval.id)
        )


def test_request_rejects_json_incompatible_input() -> None:
    _, factory, run, _ = _prepared()
    with pytest.raises(DomainValidationError):
        RequestToolInvocation(factory, FakeClock(T1)).execute(
            RequestToolInvocationCommand(run.id, "shell", {"when": T0})  # type: ignore[dict-item]
        )


def _requested(
    factory: CountingUnitOfWorkFactory, run: Run, step: RunStep | None = None
) -> ToolInvocationId:
    return RequestToolInvocation(factory, FakeClock(T1)).execute(_request(run, step)).invocation_id


def test_full_success_lifecycle_with_events() -> None:
    uow, factory, run, _ = _prepared()
    invocation_id = _requested(factory, run)
    MarkToolInvocationRunning(factory, FakeClock(T1)).execute(
        MarkToolInvocationRunningCommand(invocation_id)
    )
    result = MarkToolInvocationSucceeded(factory, FakeClock(T1)).execute(
        MarkToolInvocationSucceededCommand(invocation_id, {"stdout": "ok"})
    )
    assert result.status is ToolInvocationStatus.SUCCEEDED
    assert result.output == {"stdout": "ok"}
    assert result.output_set is True
    assert [e.type.value for e in uow.event_store.appended] == [
        "tool_invocation_requested",
        "tool_invocation_started",
        "tool_invocation_succeeded",
    ]
    assert [e.sequence for e in uow.event_store.appended] == [1, 2, 3]


def test_failure_lifecycle_preserves_structured_failure() -> None:
    uow, factory, run, _ = _prepared()
    invocation_id = _requested(factory, run)
    MarkToolInvocationRunning(factory, FakeClock(T1)).execute(
        MarkToolInvocationRunningCommand(invocation_id)
    )
    result = MarkToolInvocationFailed(factory, FakeClock(T1)).execute(
        MarkToolInvocationFailedCommand(invocation_id, FAILURE)
    )
    assert result.status is ToolInvocationStatus.FAILED
    assert result.failure == FAILURE
    event = uow.event_store.appended[-1]
    assert event.type.value == "tool_invocation_failed"
    assert event.payload == {
        "tool_invocation_id": str(invocation_id),
        "failure_code": "tool_error",
    }


def test_cancel_from_requested_and_running() -> None:
    uow, factory, run, _ = _prepared()
    first = _requested(factory, run)
    CancelToolInvocation(factory, FakeClock(T1)).execute(CancelToolInvocationCommand(first))
    assert uow.tool_repo.items[first].status is ToolInvocationStatus.CANCELLED
    second = _requested(factory, run)
    MarkToolInvocationRunning(factory, FakeClock(T1)).execute(
        MarkToolInvocationRunningCommand(second)
    )
    CancelToolInvocation(factory, FakeClock(T1)).execute(CancelToolInvocationCommand(second))
    assert uow.tool_repo.items[second].status is ToolInvocationStatus.CANCELLED


def test_running_replay_is_idempotent() -> None:
    uow, factory, run, _ = _prepared()
    invocation_id = _requested(factory, run)
    MarkToolInvocationRunning(factory, FakeClock(T1)).execute(
        MarkToolInvocationRunningCommand(invocation_id)
    )
    events = len(uow.event_store.appended)
    MarkToolInvocationRunning(factory, FakeClock(T1)).execute(
        MarkToolInvocationRunningCommand(invocation_id)
    )
    assert len(uow.event_store.appended) == events


def test_success_replay_identical_output_is_idempotent_conflicting_is_not() -> None:
    uow, factory, run, _ = _prepared()
    invocation_id = _requested(factory, run)
    MarkToolInvocationRunning(factory, FakeClock(T1)).execute(
        MarkToolInvocationRunningCommand(invocation_id)
    )
    MarkToolInvocationSucceeded(factory, FakeClock(T1)).execute(
        MarkToolInvocationSucceededCommand(invocation_id, {"stdout": "ok"})
    )
    events = len(uow.event_store.appended)
    MarkToolInvocationSucceeded(factory, FakeClock(T1)).execute(
        MarkToolInvocationSucceededCommand(invocation_id, {"stdout": "ok"})
    )
    assert len(uow.event_store.appended) == events
    with pytest.raises(EntityConflict):
        MarkToolInvocationSucceeded(factory, FakeClock(T1)).execute(
            MarkToolInvocationSucceededCommand(invocation_id, {"stdout": "different"})
        )


def test_failure_replay_identical_is_idempotent_conflicting_is_not() -> None:
    uow, factory, run, _ = _prepared()
    invocation_id = _requested(factory, run)
    MarkToolInvocationRunning(factory, FakeClock(T1)).execute(
        MarkToolInvocationRunningCommand(invocation_id)
    )
    MarkToolInvocationFailed(factory, FakeClock(T1)).execute(
        MarkToolInvocationFailedCommand(invocation_id, FAILURE)
    )
    events = len(uow.event_store.appended)
    MarkToolInvocationFailed(factory, FakeClock(T1)).execute(
        MarkToolInvocationFailedCommand(invocation_id, FAILURE)
    )
    assert len(uow.event_store.appended) == events
    with pytest.raises(EntityConflict):
        MarkToolInvocationFailed(factory, FakeClock(T1)).execute(
            MarkToolInvocationFailedCommand(
                invocation_id, Failure("other", "different", False, FailureCause.RUNTIME)
            )
        )


def test_cancel_replay_is_idempotent_and_terminal_states_conflict() -> None:
    uow, factory, run, _ = _prepared()
    invocation_id = _requested(factory, run)
    CancelToolInvocation(factory, FakeClock(T1)).execute(CancelToolInvocationCommand(invocation_id))
    events = len(uow.event_store.appended)
    CancelToolInvocation(factory, FakeClock(T1)).execute(CancelToolInvocationCommand(invocation_id))
    assert len(uow.event_store.appended) == events
    with pytest.raises(EntityConflict):
        MarkToolInvocationRunning(factory, FakeClock(T1)).execute(
            MarkToolInvocationRunningCommand(invocation_id)
        )
    with pytest.raises(EntityConflict):
        MarkToolInvocationSucceeded(factory, FakeClock(T1)).execute(
            MarkToolInvocationSucceededCommand(invocation_id, None)
        )
    with pytest.raises(EntityConflict):
        MarkToolInvocationFailed(factory, FakeClock(T1)).execute(
            MarkToolInvocationFailedCommand(invocation_id, FAILURE)
        )


def test_succeed_or_fail_from_requested_conflicts() -> None:
    _, factory, run, _ = _prepared()
    invocation_id = _requested(factory, run)
    with pytest.raises(EntityConflict):
        MarkToolInvocationSucceeded(factory, FakeClock(T1)).execute(
            MarkToolInvocationSucceededCommand(invocation_id, None)
        )
    with pytest.raises(EntityConflict):
        MarkToolInvocationFailed(factory, FakeClock(T1)).execute(
            MarkToolInvocationFailedCommand(invocation_id, FAILURE)
        )


def test_reads_and_missing_references() -> None:
    uow, factory, run, step = _prepared(with_step=True)
    assert step is not None
    run_owned = _requested(factory, run)
    step_owned = _requested(factory, run, step)
    clock = FakeClock(T1)
    assert GetToolInvocation(factory, clock).execute(run_owned).invocation_id == run_owned
    run_list = ListToolInvocationsForRun(factory, clock).execute(run.id)
    assert {r.invocation_id for r in run_list} == {run_owned, step_owned}
    step_list = ListToolInvocationsForStep(factory, clock).execute(step.id)
    assert [r.invocation_id for r in step_list] == [step_owned]
    with pytest.raises(ToolInvocationNotFound):
        GetToolInvocation(factory, clock).execute(ToolInvocationId.new())
    with pytest.raises(RunNotFound):
        ListToolInvocationsForRun(factory, clock).execute(RunId.new())
    with pytest.raises(RunStepNotFound):
        ListToolInvocationsForStep(factory, clock).execute(RunStepId.new())


def test_transition_on_invocation_with_missing_run_raises() -> None:
    uow, factory, run, _ = _prepared()
    invocation_id = _requested(factory, run)
    del uow.run_repo.items[run.id]
    with pytest.raises(RunNotFound):
        MarkToolInvocationRunning(factory, FakeClock(T1)).execute(
            MarkToolInvocationRunningCommand(invocation_id)
        )
