"""Explicit domain <-> ORM mappers, one pair of functions per entity.

No generic mapper abstraction: each entity gets its own `X_to_row`/`X_from_row`
pair so field renames or type changes surface as a type error at the call
site, not inside a shared reflection-based converter.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, cast

from friday.application.memory.models import (
    IndexSnapshot,
    IndexState,
    MemoryRetrievalItem,
    MemoryRetrievalRecord,
    RetrievalMethod,
)
from friday.application.ports import RunWorkItemView
from friday.domain import (
    ApprovalCategory,
    ApprovalRequest,
    ApprovalRequestId,
    ApprovalStatus,
    Artifact,
    ArtifactId,
    ArtifactKind,
    Conversation,
    ConversationId,
    ConversationInputMode,
    ConversationTurn,
    ConversationTurnId,
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
    Schedule,
    ScheduleFire,
    ScheduleFireId,
    ScheduleId,
    ScheduleKind,
    ScheduleStatus,
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
from friday.domain.tool_provenance import ToolProvenance
from friday.infrastructure.persistence.models import (
    ApprovalRequestRow,
    ArtifactRow,
    ConversationRow,
    ConversationTurnRow,
    MemoryIndexSnapshotRow,
    MemoryRetrievalItemRow,
    MemoryRetrievalRecordRow,
    RunEventRow,
    RunRow,
    RunStepRow,
    RunWorkItemRow,
    ScheduleFireRow,
    ScheduleRow,
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


def conversation_to_row(conversation: Conversation) -> ConversationRow:
    return ConversationRow(
        id=str(conversation.id),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def conversation_from_row(row: ConversationRow) -> Conversation:
    return Conversation(
        _id=ConversationId.parse(row.id),
        _created_at=_read_back_utc(row.created_at),
        _updated_at=_read_back_utc(row.updated_at),
    )


def conversation_turn_to_row(turn: ConversationTurn) -> ConversationTurnRow:
    return ConversationTurnRow(
        id=str(turn.id),
        conversation_id=str(turn.conversation_id),
        client_turn_id=turn.client_turn_id,
        input_text=turn.input_text,
        input_mode=turn.input_mode.value,
        recognition_language=turn.recognition_language,
        task_id=str(turn.task_id),
        run_id=str(turn.run_id),
        created_at=turn.created_at,
    )


def conversation_turn_from_row(row: ConversationTurnRow) -> ConversationTurn:
    return ConversationTurn(
        id=ConversationTurnId.parse(row.id),
        conversation_id=ConversationId.parse(row.conversation_id),
        client_turn_id=row.client_turn_id,
        input_text=row.input_text,
        input_mode=ConversationInputMode(row.input_mode),
        recognition_language=row.recognition_language,
        task_id=TaskId.parse(row.task_id),
        run_id=RunId.parse(row.run_id),
        created_at=_read_back_utc(row.created_at),
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
        execution_id=str(run.execution_id),
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
        _execution_id=RunId.parse(row.execution_id),
        _status=RunStatus(row.status),
        _created_at=_read_back_utc(row.created_at),
        _started_at=_read_back_utc(row.started_at) if row.started_at is not None else None,
        _ended_at=_read_back_utc(row.ended_at) if row.ended_at is not None else None,
        _failure=_failure_from_dict(row.failure),
        _approval_request_id=ApprovalRequestId.parse(row.approval_request_id)
        if row.approval_request_id
        else None,
    )


def schedule_to_row(schedule: Schedule) -> ScheduleRow:
    return ScheduleRow(
        id=str(schedule.id),
        task_id=str(schedule.task_id),
        kind=schedule.kind.value,
        cron=schedule.cron,
        run_at=schedule.run_at,
        timezone=schedule.timezone,
        status=schedule.status.value,
        next_fire_at=schedule.next_fire_at,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


def schedule_from_row(row: ScheduleRow) -> Schedule:
    return Schedule(
        _id=ScheduleId.parse(row.id),
        _task_id=TaskId.parse(row.task_id),
        _kind=ScheduleKind(row.kind),
        _cron=row.cron,
        _run_at=_read_back_utc(row.run_at) if row.run_at else None,
        _timezone=row.timezone,
        _status=ScheduleStatus(row.status),
        _next_fire_at=_read_back_utc(row.next_fire_at) if row.next_fire_at else None,
        _created_at=_read_back_utc(row.created_at),
        _updated_at=_read_back_utc(row.updated_at),
    )


def schedule_fire_to_row(fire: ScheduleFire) -> ScheduleFireRow:
    return ScheduleFireRow(
        id=str(fire.id),
        schedule_id=str(fire.schedule_id),
        scheduled_for=fire.scheduled_for,
        fired_at=fire.fired_at,
        run_id=str(fire.run_id),
    )


def schedule_fire_from_row(row: ScheduleFireRow) -> ScheduleFire:
    return ScheduleFire(
        id=ScheduleFireId.parse(row.id),
        schedule_id=ScheduleId.parse(row.schedule_id),
        scheduled_for=_read_back_utc(row.scheduled_for),
        fired_at=_read_back_utc(row.fired_at),
        run_id=RunId.parse(row.run_id),
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
        authorization_fingerprint=approval.authorization_fingerprint,
        consumed_at=approval.consumed_at,
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
        _authorization_fingerprint=row.authorization_fingerprint,
        _consumed_at=_read_back_utc(row.consumed_at) if row.consumed_at is not None else None,
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
    provenance = invocation.provenance
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
        provenance_kind=provenance.kind if provenance is not None else None,
        provenance_target=provenance.target if provenance is not None else None,
        provenance_remote_name=provenance.remote_name if provenance is not None else None,
        provenance_binding_fingerprint=(
            provenance.binding_fingerprint if provenance is not None else None
        ),
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
        _provenance=_provenance_from_row(row),
    )


def _provenance_from_row(row: ToolInvocationRow) -> ToolProvenance | None:
    values = (
        row.provenance_kind,
        row.provenance_target,
        row.provenance_remote_name,
        row.provenance_binding_fingerprint,
    )
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise ValueError(f"tool invocation {row.id} has partial provenance columns")
    kind, target, remote_name, fingerprint = values
    assert kind is not None and target is not None
    assert remote_name is not None and fingerprint is not None
    return ToolProvenance(
        kind=kind,
        target=target,
        remote_name=remote_name,
        binding_fingerprint=fingerprint,
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


def index_snapshot_to_row(snapshot: IndexSnapshot) -> MemoryIndexSnapshotRow:
    return MemoryIndexSnapshotRow(
        id=snapshot.id,
        vault_identity_hash=snapshot.vault_identity_hash,
        source_snapshot_hash=snapshot.source_snapshot_hash,
        graph_checksum=snapshot.graph_checksum,
        graphify_version=snapshot.graphify_version,
        status=snapshot.state.value,
        built_at=snapshot.built_at,
        file_count=snapshot.file_count,
        node_count=snapshot.node_count,
        edge_count=snapshot.edge_count,
        failure_code=snapshot.failure_code,
    )


def index_snapshot_from_row(row: MemoryIndexSnapshotRow) -> IndexSnapshot:
    # ponytail: build_duration_seconds/source_total_bytes are outside the
    # audited schema (.herdr/phase12-invariants.md smallest-schema-addition);
    # zeroed on read-back. Add columns if a caller needs them.
    return IndexSnapshot(
        id=row.id,
        vault_identity_hash=row.vault_identity_hash,
        source_snapshot_hash=row.source_snapshot_hash,
        graph_checksum=row.graph_checksum,
        graphify_version=row.graphify_version,
        state=IndexState(row.status),
        built_at=_read_back_utc(row.built_at),
        build_duration_seconds=0.0,
        file_count=row.file_count,
        source_total_bytes=0,
        node_count=row.node_count,
        edge_count=row.edge_count,
        failure_code=row.failure_code,
    )


def memory_retrieval_record_to_row(record: MemoryRetrievalRecord) -> MemoryRetrievalRecordRow:
    return MemoryRetrievalRecordRow(
        id=record.id,
        run_id=str(record.run_id),
        turn_number=record.turn_number,
        query_hash=record.query_hash,
        source_snapshot_id=record.source_snapshot_id,
        index_snapshot_id=record.index_snapshot_id,
        created_at=record.created_at,
        candidate_count=record.candidate_count,
        selected_count=record.selected_count,
    )


def memory_retrieval_record_from_row(
    row: MemoryRetrievalRecordRow, items: tuple[MemoryRetrievalItem, ...]
) -> MemoryRetrievalRecord:
    return MemoryRetrievalRecord(
        id=row.id,
        run_id=RunId.parse(row.run_id),
        turn_number=row.turn_number,
        query_hash=row.query_hash,
        source_snapshot_id=row.source_snapshot_id,
        index_snapshot_id=row.index_snapshot_id,
        created_at=_read_back_utc(row.created_at),
        candidate_count=row.candidate_count,
        selected_count=row.selected_count,
        items=items,
    )


def memory_retrieval_item_to_row(
    item: MemoryRetrievalItem, *, record_id: str
) -> MemoryRetrievalItemRow:
    return MemoryRetrievalItemRow(
        id=f"{record_id}:{item.rank}",
        record_id=record_id,
        path=item.path,
        heading=item.heading,
        start_line=item.start_line,
        end_line=item.end_line,
        content_hash=item.content_hash,
        rank=item.rank,
        methods=[method.value for method in item.methods],
        truncated=item.truncated,
    )


def memory_retrieval_item_from_row(row: MemoryRetrievalItemRow) -> MemoryRetrievalItem:
    return MemoryRetrievalItem(
        path=row.path,
        heading=row.heading,
        start_line=row.start_line,
        end_line=row.end_line,
        content_hash=row.content_hash,
        rank=row.rank,
        methods=tuple(RetrievalMethod(value) for value in cast(list[str], row.methods)),
        truncated=row.truncated,
    )
