"""Approval workflow: request coordination, four resolutions, idempotency
matrix, cross-reference validation, deterministic events."""

from __future__ import annotations

from datetime import timedelta

import pytest

from friday.application.approval_workflow import (
    ApproveRequest,
    CancelApproval,
    ExpireApproval,
    GetApproval,
    ListPendingApprovalsForRun,
    RejectRequest,
    RequestApproval,
)
from friday.application.commands import (
    ApproveRequestCommand,
    CancelApprovalCommand,
    ExpireApprovalCommand,
    RejectRequestCommand,
    RequestApprovalCommand,
)
from friday.application.errors import (
    ApprovalNotFound,
    EntityConflict,
    RunNotFound,
    RunStepNotFound,
)
from friday.domain.approval import ApprovalCategory, ApprovalStatus
from friday.domain.identifiers import ApprovalRequestId, RunId, RunStepId, TaskId
from friday.domain.run import Run, RunStatus
from friday.domain.step import RunStep, RunStepStatus
from friday.domain.task import Task
from tests.application.fakes import T0, CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork

T1 = T0 + timedelta(minutes=1)
T2 = T0 + timedelta(hours=1)


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


def _request_command(run: Run, step: RunStep | None = None) -> RequestApprovalCommand:
    return RequestApprovalCommand(
        run_id=run.id,
        category=ApprovalCategory.TOOL_EXECUTION,
        summary="run rm -rf",
        reason="cleanup",
        requested_action="delete files",
        requested_input={"path": "/tmp"},
        step_id=step.id if step else None,
        expires_at=T2,
    )


def test_request_approval_coordinates_run_and_events() -> None:
    uow, factory, run, _ = _prepared()
    result = RequestApproval(factory, FakeClock(T1)).execute(_request_command(run))
    assert result.status is ApprovalStatus.PENDING
    assert result.expires_at == T2
    assert run.status is RunStatus.WAITING_FOR_APPROVAL
    assert run.approval_request_id == result.approval_id
    assert [(e.type.value, e.sequence) for e in uow.event_store.appended] == [
        ("approval_requested", 1),
        ("run_waiting_for_approval", 2),
    ]
    assert uow.commit_count == 1


def test_request_approval_coordinates_step_scope() -> None:
    uow, factory, run, step = _prepared(with_step=True)
    assert step is not None
    result = RequestApproval(factory, FakeClock(T1)).execute(_request_command(run, step))
    assert step.status is RunStepStatus.WAITING_FOR_APPROVAL
    assert step.approval_request_id == result.approval_id
    assert all(e.step_id == step.id for e in uow.event_store.appended)


def test_request_approval_rejects_missing_run() -> None:
    _, factory, run, _ = _prepared()
    command = RequestApprovalCommand(
        run_id=RunId.new(),
        category=ApprovalCategory.OTHER,
        summary="s",
        reason="r",
        requested_action="a",
        requested_input=None,
    )
    with pytest.raises(RunNotFound):
        RequestApproval(factory, FakeClock(T1)).execute(command)


def test_request_approval_rejects_non_running_run() -> None:
    uow, factory, run, _ = _prepared()
    run.succeed(T0)
    with pytest.raises(EntityConflict):
        RequestApproval(factory, FakeClock(T1)).execute(_request_command(run))
    assert uow.commit_count == 0
    assert uow.event_store.appended == []


def test_request_approval_rejects_missing_step() -> None:
    _, factory, run, _ = _prepared()
    command = RequestApprovalCommand(
        run_id=run.id,
        category=ApprovalCategory.OTHER,
        summary="s",
        reason="r",
        requested_action="a",
        requested_input=None,
        step_id=RunStepId.new(),
    )
    with pytest.raises(RunStepNotFound):
        RequestApproval(factory, FakeClock(T1)).execute(command)


def test_request_approval_rejects_step_of_another_run() -> None:
    uow, factory, run, _ = _prepared()
    foreign = RunStep.new(
        id=RunStepId.new(), run_id=RunId.new(), name="s", position=0, created_at=T0
    )
    foreign.start(T0)
    uow.step_repo.add(foreign)
    command = RequestApprovalCommand(
        run_id=run.id,
        category=ApprovalCategory.OTHER,
        summary="s",
        reason="r",
        requested_action="a",
        requested_input=None,
        step_id=foreign.id,
    )
    with pytest.raises(EntityConflict):
        RequestApproval(factory, FakeClock(T1)).execute(command)


def test_request_approval_rejects_non_running_step() -> None:
    _, factory, run, step = _prepared(with_step=True)
    assert step is not None
    step.succeed(T0)
    with pytest.raises(EntityConflict):
        RequestApproval(factory, FakeClock(T1)).execute(_request_command(run, step))


def test_request_approval_rejects_duplicate_pending_approval() -> None:
    uow, factory, run, _ = _prepared()
    RequestApproval(factory, FakeClock(T1)).execute(_request_command(run))
    # even if the run were running again, the pending approval blocks a second
    run.resume(T1)
    with pytest.raises(EntityConflict):
        RequestApproval(factory, FakeClock(T1)).execute(_request_command(run))


def test_get_and_list_pending_approvals() -> None:
    uow, factory, run, _ = _prepared()
    clock = FakeClock(T1)
    result = RequestApproval(factory, clock).execute(_request_command(run))
    fetched = GetApproval(factory, clock).execute(result.approval_id)
    assert fetched.approval_id == result.approval_id
    pending = ListPendingApprovalsForRun(factory, clock).execute(run.id)
    assert [p.approval_id for p in pending] == [result.approval_id]
    with pytest.raises(ApprovalNotFound):
        GetApproval(factory, clock).execute(ApprovalRequestId.new())
    with pytest.raises(RunNotFound):
        ListPendingApprovalsForRun(factory, clock).execute(RunId.new())


def _pending_approval(
    factory: CountingUnitOfWorkFactory, run: Run, step: RunStep | None = None
) -> ApprovalRequestId:
    result = RequestApproval(factory, FakeClock(T1)).execute(_request_command(run, step))
    return result.approval_id


def test_approve_resumes_run_and_appends_events() -> None:
    uow, factory, run, _ = _prepared()
    approval_id = _pending_approval(factory, run)
    events_before = len(uow.event_store.appended)
    result = ApproveRequest(factory, FakeClock(T1)).execute(
        ApproveRequestCommand(approval_id, resolver="alice", resolution_note="ok")
    )
    assert result.status is ApprovalStatus.APPROVED
    assert result.resolver == "alice"
    assert run.status is RunStatus.RUNNING
    assert run.approval_request_id is None
    new_events = uow.event_store.appended[events_before:]
    assert [e.type.value for e in new_events] == ["approval_resolved", "run_resumed"]
    assert new_events[0].payload == {
        "approval_request_id": str(approval_id),
        "resolution": "approved",
    }


def test_approve_resumes_waiting_step_too() -> None:
    uow, factory, run, step = _prepared(with_step=True)
    assert step is not None
    approval_id = _pending_approval(factory, run, step)
    ApproveRequest(factory, FakeClock(T1)).execute(ApproveRequestCommand(approval_id, "alice"))
    assert step.status is RunStepStatus.RUNNING
    assert step.approval_request_id is None
    assert run.status is RunStatus.RUNNING


def test_reject_also_resumes_run() -> None:
    uow, factory, run, _ = _prepared()
    approval_id = _pending_approval(factory, run)
    result = RejectRequest(factory, FakeClock(T1)).execute(
        RejectRequestCommand(approval_id, resolver="bob", resolution_note="too risky")
    )
    assert result.status is ApprovalStatus.REJECTED
    assert result.resolution_note == "too risky"
    assert run.status is RunStatus.RUNNING


def test_cancel_approval_resumes_run_without_resolver() -> None:
    uow, factory, run, _ = _prepared()
    approval_id = _pending_approval(factory, run)
    result = CancelApproval(factory, FakeClock(T1)).execute(
        CancelApprovalCommand(approval_id, resolution_note="superseded")
    )
    assert result.status is ApprovalStatus.CANCELLED
    assert result.resolver is None
    assert run.status is RunStatus.RUNNING


def test_expire_after_deadline_resumes_run() -> None:
    uow, factory, run, _ = _prepared()
    approval_id = _pending_approval(factory, run)
    result = ExpireApproval(factory, FakeClock(T2)).execute(ExpireApprovalCommand(approval_id))
    assert result.status is ApprovalStatus.EXPIRED
    assert run.status is RunStatus.RUNNING


def test_expire_before_deadline_conflicts() -> None:
    uow, factory, run, _ = _prepared()
    approval_id = _pending_approval(factory, run)
    with pytest.raises(EntityConflict):
        ExpireApproval(factory, FakeClock(T1)).execute(ExpireApprovalCommand(approval_id))
    assert uow.approval_repo.items[approval_id].status is ApprovalStatus.PENDING


def test_expire_without_deadline_conflicts() -> None:
    uow, factory, run, _ = _prepared()
    command = RequestApprovalCommand(
        run_id=run.id,
        category=ApprovalCategory.OTHER,
        summary="s",
        reason="r",
        requested_action="a",
        requested_input=None,
    )
    approval_id = RequestApproval(factory, FakeClock(T1)).execute(command).approval_id
    with pytest.raises(EntityConflict):
        ExpireApproval(factory, FakeClock(T2)).execute(ExpireApprovalCommand(approval_id))


def test_resolution_leaves_non_waiting_entities_untouched() -> None:
    """A run cancelled while the approval was pending is not resumed."""
    uow, factory, run, _ = _prepared()
    approval_id = _pending_approval(factory, run)
    run.cancel(T1)
    result = CancelApproval(factory, FakeClock(T1)).execute(CancelApprovalCommand(approval_id))
    assert result.status is ApprovalStatus.CANCELLED
    assert run.status is RunStatus.CANCELLED
    assert uow.event_store.appended[-1].type.value == "approval_resolved"


def test_same_terminal_replay_is_idempotent_without_new_events() -> None:
    uow, factory, run, _ = _prepared()
    approval_id = _pending_approval(factory, run)
    clock = FakeClock(T1)
    first = ApproveRequest(factory, clock).execute(
        ApproveRequestCommand(approval_id, "alice", "ok")
    )
    events = len(uow.event_store.appended)
    replay = ApproveRequest(factory, FakeClock(T2)).execute(
        ApproveRequestCommand(approval_id, "mallory", "different")
    )
    assert replay == first  # timestamps, resolver, note all unchanged
    assert len(uow.event_store.appended) == events


@pytest.mark.parametrize("resolution", ["reject", "cancel", "expire"])
def test_conflicting_terminal_resolution_is_stable_conflict(resolution: str) -> None:
    uow, factory, run, _ = _prepared()
    approval_id = _pending_approval(factory, run)
    ApproveRequest(factory, FakeClock(T1)).execute(ApproveRequestCommand(approval_id, "alice"))
    events = len(uow.event_store.appended)
    with pytest.raises(EntityConflict):
        if resolution == "reject":
            RejectRequest(factory, FakeClock(T2)).execute(RejectRequestCommand(approval_id, "bob"))
        elif resolution == "cancel":
            CancelApproval(factory, FakeClock(T2)).execute(CancelApprovalCommand(approval_id))
        else:
            ExpireApproval(factory, FakeClock(T2)).execute(ExpireApprovalCommand(approval_id))
    assert len(uow.event_store.appended) == events
    assert uow.approval_repo.items[approval_id].status is ApprovalStatus.APPROVED


@pytest.mark.parametrize("resolution", ["approve", "reject", "cancel", "expire"])
def test_resolving_missing_approval_raises_not_found(resolution: str) -> None:
    _, factory, _, _ = _prepared()
    missing = ApprovalRequestId.new()
    clock = FakeClock(T2)
    with pytest.raises(ApprovalNotFound):
        if resolution == "approve":
            ApproveRequest(factory, clock).execute(ApproveRequestCommand(missing, "a"))
        elif resolution == "reject":
            RejectRequest(factory, clock).execute(RejectRequestCommand(missing, "a"))
        elif resolution == "cancel":
            CancelApproval(factory, clock).execute(CancelApprovalCommand(missing))
        else:
            ExpireApproval(factory, clock).execute(ExpireApprovalCommand(missing))


def test_resolving_approval_of_missing_run_raises() -> None:
    uow, factory, run, _ = _prepared()
    approval_id = _pending_approval(factory, run)
    del uow.run_repo.items[run.id]
    with pytest.raises(RunNotFound):
        ApproveRequest(factory, FakeClock(T1)).execute(ApproveRequestCommand(approval_id, "a"))


def test_resolving_approval_of_missing_step_raises() -> None:
    uow, factory, run, step = _prepared(with_step=True)
    assert step is not None
    approval_id = _pending_approval(factory, run, step)
    del uow.step_repo.items[step.id]
    with pytest.raises(RunStepNotFound):
        ApproveRequest(factory, FakeClock(T1)).execute(ApproveRequestCommand(approval_id, "a"))


def test_request_approval_removes_the_active_work_item() -> None:
    uow, factory, run, _ = _prepared()
    uow.work_queue_repo.enqueue(run.id, available_at=T0, enqueued_at=T0)
    RequestApproval(factory, FakeClock(T1)).execute(_request_command(run))
    assert uow.work_queue_repo.get(run.id) is None


def test_approve_re_enqueues_the_resumed_run_as_due() -> None:
    uow, factory, run, _ = _prepared()
    approval_id = _pending_approval(factory, run)
    ApproveRequest(factory, FakeClock(T1)).execute(
        ApproveRequestCommand(approval_id, resolver="alice", resolution_note="ok")
    )
    work_item = uow.work_queue_repo.get(run.id)
    assert work_item is not None
    assert work_item.available_at == T1
    assert work_item.claimed_by is None
