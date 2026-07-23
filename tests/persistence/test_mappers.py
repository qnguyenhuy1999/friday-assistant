from __future__ import annotations

from datetime import UTC, datetime, timedelta

from friday.domain import (
    ApprovalCategory,
    ApprovalRequest,
    ApprovalRequestId,
    Artifact,
    ArtifactId,
    ArtifactKind,
    Failure,
    FailureCause,
    Run,
    RunEvent,
    RunEventId,
    RunEventType,
    RunId,
    RunStep,
    RunStepId,
    Task,
    TaskId,
    ToolInvocation,
    ToolInvocationId,
)
from friday.infrastructure.persistence.mappers import (
    approval_from_row,
    approval_to_row,
    artifact_from_row,
    artifact_to_row,
    run_event_from_row,
    run_event_to_row,
    run_from_row,
    run_step_from_row,
    run_step_to_row,
    run_to_row,
    task_from_row,
    task_to_row,
    tool_invocation_from_row,
    tool_invocation_to_row,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_task_round_trips_through_terminal_state() -> None:
    task = Task.new(id=TaskId.new(), title="t", description="d", created_at=T0)
    task.start(T0)
    task.fail(T0, Failure(code="E", message="m", retryable=False, cause=FailureCause.RUNTIME))
    restored = task_from_row(task_to_row(task))
    assert restored.id == task.id
    assert restored.status == task.status
    assert restored.failure == task.failure


def test_run_round_trips_through_waiting_for_approval() -> None:
    run = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)
    run.start(T0)
    run.wait_for_approval(T0, ApprovalRequestId.new())
    restored = run_from_row(run_to_row(run))
    assert restored.id == run.id
    assert restored.status == run.status
    assert restored.approval_request_id == run.approval_request_id


def test_run_step_round_trips_through_failed_state() -> None:
    step = RunStep.new(id=RunStepId.new(), run_id=RunId.new(), name="s", position=0, created_at=T0)
    step.start(T0)
    step.fail(T0, Failure(code="E", message="m", retryable=True, cause=FailureCause.TOOL))
    restored = run_step_from_row(run_step_to_row(step))
    assert restored.id == step.id
    assert restored.status == step.status
    assert restored.failure == step.failure


def test_approval_request_round_trips_expiry_deadline() -> None:
    approval = ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=RunId.new(),
        category=ApprovalCategory.TOOL_EXECUTION,
        summary="s",
        reason="r",
        requested_action="a",
        requested_input=None,
        requested_at=T0,
        expires_at=T0 + timedelta(hours=1),
    )
    restored = approval_from_row(approval_to_row(approval))
    assert restored.expires_at == approval.expires_at


def test_approval_request_round_trips_through_approved_state() -> None:
    approval = ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=RunId.new(),
        category=ApprovalCategory.TOOL_EXECUTION,
        summary="s",
        reason="r",
        requested_action="a",
        requested_input={"x": 1},
        requested_at=T0,
    )
    approval.approve(T0, resolver="alice", resolution_note="ok")
    restored = approval_from_row(approval_to_row(approval))
    assert restored.id == approval.id
    assert restored.status == approval.status
    assert restored.resolver == approval.resolver
    assert restored.resolution_note == approval.resolution_note


def test_artifact_round_trips() -> None:
    artifact = Artifact(
        id=ArtifactId.new(),
        run_id=RunId.new(),
        kind=ArtifactKind.FILE,
        name="n",
        media_type="text/plain",
        location="/tmp/x",
        created_at=T0,
        size=10,
        checksum="abc",
        metadata={"k": "v"},
    )
    restored = artifact_from_row(artifact_to_row(artifact))
    assert restored == artifact


def test_tool_invocation_round_trips_output_set_flag_when_output_is_none() -> None:
    invocation = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=RunId.new(),
        tool_name="x",
        requested_input=None,
        requested_at=T0,
    )
    invocation.start(T0)
    invocation.succeed(T0, output=None)
    restored = tool_invocation_from_row(tool_invocation_to_row(invocation))
    assert restored.output_set is True
    assert restored.output is None


def test_run_event_round_trips() -> None:
    event = RunEvent(
        id=RunEventId.new(),
        run_id=RunId.new(),
        type=RunEventType.RUN_CREATED,
        sequence=1,
        occurred_at=T0,
        payload={"k": "v"},
    )
    restored = run_event_from_row(run_event_to_row(event))
    assert restored == event
