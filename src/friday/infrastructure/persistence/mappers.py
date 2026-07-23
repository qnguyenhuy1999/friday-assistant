"""Explicit domain <-> ORM mappers, one pair of functions per entity.

No generic mapper abstraction: each entity gets its own `X_to_row`/`X_from_row`
pair so field renames or type changes surface as a type error at the call
site, not inside a shared reflection-based converter.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, cast

from friday.domain import (
    ApprovalCategory,
    ApprovalRequest,
    ApprovalRequestId,
    ApprovalStatus,
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
    RunStatus,
    RunStep,
    RunStepId,
    RunStepStatus,
    Task,
    TaskId,
    TaskStatus,
    ToolInvocation,
    ToolInvocationId,
    ToolInvocationStatus,
)
from friday.domain.json_value import JsonValue
from friday.infrastructure.persistence.models import (
    ApprovalRequestRow,
    ArtifactRow,
    RunEventRow,
    RunRow,
    RunStepRow,
    TaskRow,
    ToolInvocationRow,
)


def _failure_to_dict(failure: Failure | None) -> dict[str, Any] | None:
    return asdict(failure) if failure is not None else None


def _failure_from_dict(data: dict[str, Any] | None) -> Failure | None:
    if data is None:
        return None
    return Failure(
        code=data["code"],
        message=data["message"],
        retryable=data["retryable"],
        cause=FailureCause(data["cause"]),
        details=data["details"],
    )


def task_to_row(task: Task) -> TaskRow:
    return TaskRow(
        id=str(task.id),
        title=task.title,
        description=task.description,
        status=task.status.value,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        failed_at=task.failed_at,
        cancelled_at=task.cancelled_at,
        failure=_failure_to_dict(task.failure),
    )


def task_from_row(row: TaskRow) -> Task:
    return Task(
        _id=TaskId.parse(row.id),
        _title=row.title,
        _description=row.description,
        _status=TaskStatus(row.status),
        _created_at=row.created_at,
        _started_at=row.started_at,
        _completed_at=row.completed_at,
        _failed_at=row.failed_at,
        _cancelled_at=row.cancelled_at,
        _failure=_failure_from_dict(row.failure),
    )


def run_to_row(run: Run) -> RunRow:
    return RunRow(
        id=str(run.id),
        task_id=str(run.task_id),
        status=run.status.value,
        created_at=run.created_at,
        started_at=run.started_at,
        ended_at=run.ended_at,
        failure=_failure_to_dict(run.failure),
        approval_request_id=str(run.approval_request_id) if run.approval_request_id else None,
    )


def run_from_row(row: RunRow) -> Run:
    return Run(
        _id=RunId.parse(row.id),
        _task_id=TaskId.parse(row.task_id),
        _status=RunStatus(row.status),
        _created_at=row.created_at,
        _started_at=row.started_at,
        _ended_at=row.ended_at,
        _failure=_failure_from_dict(row.failure),
        _approval_request_id=ApprovalRequestId.parse(row.approval_request_id)
        if row.approval_request_id
        else None,
    )


def run_step_to_row(step: RunStep) -> RunStepRow:
    return RunStepRow(
        id=str(step.id),
        run_id=str(step.run_id),
        name=step.name,
        position=step.position,
        status=step.status.value,
        created_at=step.created_at,
        started_at=step.started_at,
        ended_at=step.ended_at,
        failure=_failure_to_dict(step.failure),
        approval_request_id=str(step.approval_request_id) if step.approval_request_id else None,
    )


def run_step_from_row(row: RunStepRow) -> RunStep:
    return RunStep(
        _id=RunStepId.parse(row.id),
        _run_id=RunId.parse(row.run_id),
        _name=row.name,
        _position=row.position,
        _status=RunStepStatus(row.status),
        _created_at=row.created_at,
        _started_at=row.started_at,
        _ended_at=row.ended_at,
        _failure=_failure_from_dict(row.failure),
        _approval_request_id=ApprovalRequestId.parse(row.approval_request_id)
        if row.approval_request_id
        else None,
    )


def approval_to_row(approval: ApprovalRequest) -> ApprovalRequestRow:
    return ApprovalRequestRow(
        id=str(approval.id),
        run_id=str(approval.run_id),
        step_id=str(approval.step_id) if approval.step_id else None,
        category=approval.category.value,
        summary=approval.summary,
        reason=approval.reason,
        requested_action=approval.requested_action,
        requested_input=approval.requested_input,
        status=approval.status.value,
        requested_at=approval.requested_at,
        resolved_at=approval.resolved_at,
        resolution_note=approval.resolution_note,
        resolver=approval.resolver,
    )


def approval_from_row(row: ApprovalRequestRow) -> ApprovalRequest:
    return ApprovalRequest(
        _id=ApprovalRequestId.parse(row.id),
        _run_id=RunId.parse(row.run_id),
        _step_id=RunStepId.parse(row.step_id) if row.step_id else None,
        _category=ApprovalCategory(row.category),
        _summary=row.summary,
        _reason=row.reason,
        _requested_action=row.requested_action,
        _requested_input=cast(JsonValue, row.requested_input),
        _status=ApprovalStatus(row.status),
        _requested_at=row.requested_at,
        _resolved_at=row.resolved_at,
        _resolution_note=row.resolution_note,
        _resolver=row.resolver,
    )


def artifact_to_row(artifact: Artifact) -> ArtifactRow:
    return ArtifactRow(
        id=str(artifact.id),
        run_id=str(artifact.run_id),
        step_id=str(artifact.step_id) if artifact.step_id else None,
        kind=artifact.kind.value,
        name=artifact.name,
        media_type=artifact.media_type,
        location=artifact.location,
        created_at=artifact.created_at,
        size=artifact.size,
        checksum=artifact.checksum,
        artifact_metadata=artifact.metadata,
    )


def artifact_from_row(row: ArtifactRow) -> Artifact:
    return Artifact(
        id=ArtifactId.parse(row.id),
        run_id=RunId.parse(row.run_id),
        step_id=RunStepId.parse(row.step_id) if row.step_id else None,
        kind=ArtifactKind(row.kind),
        name=row.name,
        media_type=row.media_type,
        location=row.location,
        created_at=row.created_at,
        size=row.size,
        checksum=row.checksum,
        metadata=cast(JsonValue, row.artifact_metadata),
    )


def tool_invocation_to_row(invocation: ToolInvocation) -> ToolInvocationRow:
    return ToolInvocationRow(
        id=str(invocation.id),
        run_id=str(invocation.run_id),
        step_id=str(invocation.step_id) if invocation.step_id else None,
        approval_request_id=str(invocation.approval_request_id)
        if invocation.approval_request_id
        else None,
        tool_name=invocation.tool_name,
        requested_input=invocation.requested_input,
        status=invocation.status.value,
        requested_at=invocation.requested_at,
        started_at=invocation.started_at,
        completed_at=invocation.completed_at,
        output=invocation.output,
        output_set=invocation.output_set,
        failure=_failure_to_dict(invocation.failure),
    )


def tool_invocation_from_row(row: ToolInvocationRow) -> ToolInvocation:
    return ToolInvocation(
        _id=ToolInvocationId.parse(row.id),
        _run_id=RunId.parse(row.run_id),
        _step_id=RunStepId.parse(row.step_id) if row.step_id else None,
        _approval_request_id=ApprovalRequestId.parse(row.approval_request_id)
        if row.approval_request_id
        else None,
        _tool_name=row.tool_name,
        _requested_input=cast(JsonValue, row.requested_input),
        _status=ToolInvocationStatus(row.status),
        _requested_at=row.requested_at,
        _started_at=row.started_at,
        _completed_at=row.completed_at,
        _output=cast(JsonValue, row.output),
        _output_set=row.output_set,
        _failure=_failure_from_dict(row.failure),
    )


def run_event_to_row(event: RunEvent) -> RunEventRow:
    return RunEventRow(
        id=str(event.id),
        run_id=str(event.run_id),
        step_id=str(event.step_id) if event.step_id else None,
        type=event.type.value,
        sequence=event.sequence,
        occurred_at=event.occurred_at,
        payload=event.payload,
    )


def run_event_from_row(row: RunEventRow) -> RunEvent:
    return RunEvent(
        id=RunEventId.parse(row.id),
        run_id=RunId.parse(row.run_id),
        step_id=RunStepId.parse(row.step_id) if row.step_id else None,
        type=RunEventType(row.type),
        sequence=row.sequence,
        occurred_at=row.occurred_at,
        payload=cast(JsonValue, row.payload),
    )
