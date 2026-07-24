"""Explicit domain <-> ORM mappers, one pair of functions per entity.

No generic mapper abstraction: each entity gets its own `X_to_row`/`X_from_row`
pair so field renames or type changes surface as a type error at the call
site, not inside a shared reflection-based converter.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, cast

from friday.application.ports import RunWorkItemView
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
    TaskEvent,
    TaskEventId,
    TaskEventType,
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
    RunWorkItemRow,
    TaskEventRow,
    TaskRow,
    ToolInvocationRow,
)


def _failure_to_dict(failure: Failure | None) -> dict[str, Any] | None:
    return asdict(failure) if failure is not None else None


def _read_back_utc(value: datetime) -> datetime:
    """Reattach UTC tzinfo SQLite drops on read-back (values are always
    written UTC-normalized, so a naive read-back is safely reinterpreted).
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


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
        _created_at=_read_back_utc(row.created_at),
        _started_at=_read_back_utc(row.started_at) if row.started_at is not None else None,
        _completed_at=_read_back_utc(row.completed_at) if row.completed_at is not None else None,
        _failed_at=_read_back_utc(row.failed_at) if row.failed_at is not None else None,
        _cancelled_at=_read_back_utc(row.cancelled_at) if row.cancelled_at is not None else None,
        _failure=_failure_from_dict(row.failure),
    )


def task_event_to_row(event: TaskEvent) -> TaskEventRow:
    return TaskEventRow(
        id=str(event.id),
        task_id=str(event.task_id),
        type=event.type.value,
        sequence=event.sequence,
        occurred_at=event.occurred_at,
        payload=event.payload,
    )


def task_event_from_row(row: TaskEventRow) -> TaskEvent:
    return TaskEvent(
        id=TaskEventId.parse(row.id),
        task_id=TaskId.parse(row.task_id),
        type=TaskEventType(row.type),
        sequence=row.sequence,
        occurred_at=_read_back_utc(row.occurred_at),
        payload=cast(JsonValue, row.payload),
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
        _created_at=_read_back_utc(row.created_at),
        _started_at=_read_back_utc(row.started_at) if row.started_at is not None else None,
        _ended_at=_read_back_utc(row.ended_at) if row.ended_at is not None else None,
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
        _created_at=_read_back_utc(row.created_at),
        _started_at=_read_back_utc(row.started_at) if row.started_at is not None else None,
        _ended_at=_read_back_utc(row.ended_at) if row.ended_at is not None else None,
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
        expires_at=approval.expires_at,
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
        _requested_at=_read_back_utc(row.requested_at),
        _expires_at=_read_back_utc(row.expires_at) if row.expires_at is not None else None,
        _resolved_at=_read_back_utc(row.resolved_at) if row.resolved_at is not None else None,
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
        created_at=_read_back_utc(row.created_at),
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
        _requested_at=_read_back_utc(row.requested_at),
        _started_at=_read_back_utc(row.started_at) if row.started_at is not None else None,
        _completed_at=_read_back_utc(row.completed_at) if row.completed_at is not None else None,
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
        occurred_at=_read_back_utc(row.occurred_at),
        payload=cast(JsonValue, row.payload),
    )


def run_work_item_from_row(row: RunWorkItemRow) -> RunWorkItemView:
    return RunWorkItemView(
        run_id=RunId.parse(row.run_id),
        available_at=_read_back_utc(row.available_at),
        enqueued_at=_read_back_utc(row.enqueued_at),
        claimed_by=row.claimed_by,
        claim_token=row.claim_token,
        claim_generation=row.claim_generation,
        claimed_at=_read_back_utc(row.claimed_at) if row.claimed_at is not None else None,
        heartbeat_at=_read_back_utc(row.heartbeat_at) if row.heartbeat_at is not None else None,
        lease_expires_at=_read_back_utc(row.lease_expires_at)
        if row.lease_expires_at is not None
        else None,
    )
