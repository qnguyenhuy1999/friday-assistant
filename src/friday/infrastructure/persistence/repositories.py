from __future__ import annotations

import builtins
from typing import Any, cast

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from friday.application.memory.models import IndexSnapshot, IndexState, MemoryRetrievalRecord
from friday.domain import (
    ApprovalRequest,
    ApprovalRequestId,
    ApprovalStatus,
    Artifact,
    ArtifactId,
    Conversation,
    ConversationId,
    ConversationTurn,
    ConversationTurnId,
    Run,
    RunEvent,
    RunEventType,
    RunId,
    RunStep,
    RunStepId,
    Schedule,
    ScheduleFire,
    ScheduleId,
    Task,
    TaskEvent,
    TaskId,
    ToolInvocation,
    ToolInvocationId,
)
from friday.domain.run import TERMINAL_RUN_STATUSES
from friday.domain.schedule import ScheduleStatus
from friday.domain.step import TERMINAL_RUN_STEP_STATUSES
from friday.domain.tool import TERMINAL_TOOL_INVOCATION_STATUSES
from friday.infrastructure.persistence.mappers import (
    approval_from_row,
    approval_to_row,
    artifact_from_row,
    artifact_to_row,
    conversation_from_row,
    conversation_to_row,
    conversation_turn_from_row,
    conversation_turn_to_row,
    index_snapshot_from_row,
    index_snapshot_to_row,
    memory_retrieval_item_from_row,
    memory_retrieval_item_to_row,
    memory_retrieval_record_from_row,
    memory_retrieval_record_to_row,
    run_event_from_row,
    run_event_to_row,
    run_from_row,
    run_step_from_row,
    run_step_to_row,
    run_to_row,
    schedule_fire_from_row,
    schedule_fire_to_row,
    schedule_from_row,
    schedule_to_row,
    task_event_from_row,
    task_event_to_row,
    task_from_row,
    task_to_row,
    tool_invocation_from_row,
    tool_invocation_to_row,
)
from friday.infrastructure.persistence.models import (
    ApprovalRequestRow,
    ArtifactRow,
    ConversationRow,
    ConversationTurnRow,
    MemoryIndexSnapshotRow,
    MemoryRetrievalItemRow,
    MemoryRetrievalRecordRow,
    RunEventRow,
    RunEventSequenceCounterRow,
    RunRow,
    RunStepRow,
    ScheduleFireRow,
    ScheduleRow,
    TaskEventRow,
    TaskEventSequenceCounterRow,
    TaskRow,
    ToolInvocationRow,
)


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, task: Task) -> None:
        self._session.add(task_to_row(task))

    def get(self, task_id: TaskId) -> Task | None:
        row = self._session.get(TaskRow, str(task_id))
        return task_from_row(row) if row is not None else None

    def save(self, task: Task) -> None:
        self._session.merge(task_to_row(task))

    def list(self, limit: int) -> list[Task]:
        stmt = select(TaskRow).order_by(TaskRow.created_at, TaskRow.id).limit(limit)
        return [task_from_row(row) for row in self._session.execute(stmt).scalars()]

    def list_page(
        self, limit: int, after_created_at: object | None, after_id: str | None
    ) -> builtins.list[Task]:
        stmt = select(TaskRow)
        if after_created_at is not None and after_id is not None:
            stmt = stmt.where(
                or_(
                    TaskRow.created_at > after_created_at,
                    and_(TaskRow.created_at == after_created_at, TaskRow.id > after_id),
                )
            )
        return [
            task_from_row(row)
            for row in self._session.execute(
                stmt.order_by(TaskRow.created_at, TaskRow.id).limit(limit)
            ).scalars()
        ]


class RunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, run: Run) -> None:
        self._session.add(run_to_row(run))

    def get(self, run_id: RunId) -> Run | None:
        row = self._session.get(RunRow, str(run_id))
        return run_from_row(row) if row is not None else None

    def save(self, run: Run) -> None:
        self._session.merge(run_to_row(run))

    def list_for_task(self, task_id: TaskId) -> list[Run]:
        stmt = (
            select(RunRow)
            .where(RunRow.task_id == str(task_id))
            .order_by(RunRow.created_at, RunRow.id)
        )
        return [run_from_row(row) for row in self._session.execute(stmt).scalars()]

    def list_for_task_page(
        self, task_id: TaskId, limit: int, after_created_at: object | None, after_id: str | None
    ) -> list[Run]:
        stmt = select(RunRow).where(RunRow.task_id == str(task_id))
        if after_created_at is not None and after_id is not None:
            stmt = stmt.where(
                or_(
                    RunRow.created_at > after_created_at,
                    and_(RunRow.created_at == after_created_at, RunRow.id > after_id),
                )
            )
        return [
            run_from_row(row)
            for row in self._session.execute(
                stmt.order_by(RunRow.created_at, RunRow.id).limit(limit)
            ).scalars()
        ]

    def has_non_terminal_for_ids(self, run_ids: list[RunId]) -> bool:
        if not run_ids:
            return False
        stmt = select(RunRow.id).where(
            RunRow.id.in_([str(x) for x in run_ids]),
            RunRow.status.not_in(tuple(x.value for x in TERMINAL_RUN_STATUSES)),
        )
        return self._session.execute(stmt.limit(1)).first() is not None

    def count_for_execution(self, execution_id: RunId) -> int:
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(RunRow)
                .where(RunRow.execution_id == str(execution_id))
            )
            or 0
        )

    def list_for_execution(self, execution_id: RunId) -> list[Run]:
        stmt = (
            select(RunRow)
            .where(RunRow.execution_id == str(execution_id))
            .order_by(RunRow.created_at, RunRow.id)
        )
        return [run_from_row(row) for row in self._session.execute(stmt).scalars()]

    def get_latest_for_execution(self, execution_id: RunId) -> Run | None:
        stmt = (
            select(RunRow)
            .where(RunRow.execution_id == str(execution_id))
            .order_by(RunRow.created_at.desc(), RunRow.id.desc())
            .limit(1)
        )
        row = self._session.execute(stmt).scalars().first()
        return run_from_row(row) if row is not None else None


class ScheduleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, schedule: Schedule) -> None:
        self._session.add(schedule_to_row(schedule))

    def get(self, schedule_id: ScheduleId) -> Schedule | None:
        row = self._session.get(ScheduleRow, str(schedule_id))
        return schedule_from_row(row) if row else None

    def save(self, schedule: Schedule) -> None:
        self._session.merge(schedule_to_row(schedule))

    def list_for_task(self, task_id: TaskId, limit: int) -> list[Schedule]:
        stmt = (
            select(ScheduleRow)
            .where(ScheduleRow.task_id == str(task_id))
            .order_by(ScheduleRow.created_at, ScheduleRow.id)
            .limit(limit)
        )
        return [schedule_from_row(x) for x in self._session.execute(stmt).scalars()]

    def list_for_task_page(
        self, task_id: TaskId, limit: int, after_created_at: object | None, after_id: str | None
    ) -> list[Schedule]:
        stmt = select(ScheduleRow).where(ScheduleRow.task_id == str(task_id))
        if after_created_at is not None and after_id is not None:
            stmt = stmt.where(
                or_(
                    ScheduleRow.created_at > after_created_at,
                    and_(ScheduleRow.created_at == after_created_at, ScheduleRow.id > after_id),
                )
            )
        rows = self._session.execute(
            stmt.order_by(ScheduleRow.created_at, ScheduleRow.id).limit(limit)
        ).scalars()
        return [schedule_from_row(x) for x in rows]

    def list_due(self, now: object, limit: int) -> list[Schedule]:
        stmt = (
            select(ScheduleRow)
            .where(
                ScheduleRow.status == ScheduleStatus.ACTIVE.value,
                ScheduleRow.next_fire_at <= now,
            )
            .order_by(ScheduleRow.next_fire_at, ScheduleRow.id)
            .limit(limit)
        )
        return [schedule_from_row(x) for x in self._session.execute(stmt).scalars()]

    def complete_for_task(self, task_id: TaskId, at: object, *, cancelled: bool) -> None:
        status = ScheduleStatus.CANCELLED.value if cancelled else ScheduleStatus.COMPLETED.value
        self._session.execute(
            update(ScheduleRow)
            .where(
                ScheduleRow.task_id == str(task_id),
                ScheduleRow.status.in_((ScheduleStatus.ACTIVE.value, ScheduleStatus.PAUSED.value)),
            )
            .values(status=status, next_fire_at=None, updated_at=at)
        )


class ConversationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, conversation: Conversation) -> None:
        self._session.add(conversation_to_row(conversation))

    def get(self, conversation_id: ConversationId) -> Conversation | None:
        row = self._session.get(ConversationRow, str(conversation_id))
        return conversation_from_row(row) if row is not None else None

    def save(self, conversation: Conversation) -> None:
        self._session.merge(conversation_to_row(conversation))


class ConversationTurnRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, turn: ConversationTurn) -> None:
        self._session.add(conversation_turn_to_row(turn))

    def get(self, turn_id: ConversationTurnId) -> ConversationTurn | None:
        row = self._session.get(ConversationTurnRow, str(turn_id))
        return conversation_turn_from_row(row) if row is not None else None

    def get_by_client_turn_id(
        self, conversation_id: ConversationId, client_turn_id: str
    ) -> ConversationTurn | None:
        row = self._session.scalar(
            select(ConversationTurnRow).where(
                ConversationTurnRow.conversation_id == str(conversation_id),
                ConversationTurnRow.client_turn_id == client_turn_id,
            )
        )
        return conversation_turn_from_row(row) if row is not None else None

    def get_by_run(self, run_id: RunId) -> ConversationTurn | None:
        row = self._session.scalar(
            select(ConversationTurnRow).where(ConversationTurnRow.run_id == str(run_id))
        )
        return conversation_turn_from_row(row) if row is not None else None

    def list_for_conversation_page(
        self,
        conversation_id: ConversationId,
        limit: int,
        after_created_at: object | None,
        after_id: str | None,
    ) -> list[ConversationTurn]:
        stmt = select(ConversationTurnRow).where(
            ConversationTurnRow.conversation_id == str(conversation_id)
        )
        if after_created_at is not None and after_id is not None:
            stmt = stmt.where(
                or_(
                    ConversationTurnRow.created_at > after_created_at,
                    and_(
                        ConversationTurnRow.created_at == after_created_at,
                        ConversationTurnRow.id > after_id,
                    ),
                )
            )
        return [
            conversation_turn_from_row(row)
            for row in self._session.execute(
                stmt.order_by(ConversationTurnRow.created_at, ConversationTurnRow.id).limit(limit)
            ).scalars()
        ]

    def list_recent_before(
        self,
        conversation_id: ConversationId,
        before_created_at: object | None,
        before_id: str | None,
        limit: int,
    ) -> list[ConversationTurn]:
        stmt = select(ConversationTurnRow).where(
            ConversationTurnRow.conversation_id == str(conversation_id)
        )
        if before_created_at is not None and before_id is not None:
            stmt = stmt.where(
                or_(
                    ConversationTurnRow.created_at < before_created_at,
                    and_(
                        ConversationTurnRow.created_at == before_created_at,
                        ConversationTurnRow.id < before_id,
                    ),
                )
            )
        rows = self._session.execute(
            stmt.order_by(
                ConversationTurnRow.created_at.desc(), ConversationTurnRow.id.desc()
            ).limit(limit)
        ).scalars()
        return [conversation_turn_from_row(row) for row in reversed(list(rows))]


class ScheduleFireRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, fire: ScheduleFire) -> None:
        self._session.add(schedule_fire_to_row(fire))

    def list_for_schedule(self, schedule_id: ScheduleId, limit: int) -> list[ScheduleFire]:
        stmt = (
            select(ScheduleFireRow)
            .where(ScheduleFireRow.schedule_id == str(schedule_id))
            .order_by(ScheduleFireRow.scheduled_for, ScheduleFireRow.id)
            .limit(limit)
        )
        return [schedule_fire_from_row(x) for x in self._session.execute(stmt).scalars()]

    def list_for_schedule_page(
        self,
        schedule_id: ScheduleId,
        limit: int,
        after_scheduled_for: object | None,
        after_id: str | None,  # noqa: E501
    ) -> list[ScheduleFire]:
        stmt = select(ScheduleFireRow).where(ScheduleFireRow.schedule_id == str(schedule_id))
        if after_scheduled_for is not None and after_id is not None:
            stmt = stmt.where(
                or_(
                    ScheduleFireRow.scheduled_for > after_scheduled_for,
                    and_(
                        ScheduleFireRow.scheduled_for == after_scheduled_for,
                        ScheduleFireRow.id > after_id,
                    ),
                )
            )
        stmt = stmt.order_by(ScheduleFireRow.scheduled_for, ScheduleFireRow.id).limit(limit)
        return [schedule_fire_from_row(x) for x in self._session.execute(stmt).scalars()]

    def has_non_terminal_execution_for_schedule(self, schedule_id: ScheduleId) -> bool:
        # A fire points to the root attempt. All retry descendants share its
        # execution_id, so this remains bounded regardless of fire history.
        stmt = (
            select(RunRow.id)
            .join(ScheduleFireRow, RunRow.execution_id == ScheduleFireRow.run_id)
            .where(
                ScheduleFireRow.schedule_id == str(schedule_id),
                RunRow.status.not_in(tuple(x.value for x in TERMINAL_RUN_STATUSES)),
            )
            .limit(1)
        )
        return self._session.execute(stmt).first() is not None


class RunStepRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, step: RunStep) -> None:
        self._session.add(run_step_to_row(step))

    def get(self, step_id: RunStepId) -> RunStep | None:
        row = self._session.get(RunStepRow, str(step_id))
        return run_step_from_row(row) if row is not None else None

    def save(self, step: RunStep) -> None:
        self._session.merge(run_step_to_row(step))

    def list_for_run(self, run_id: RunId) -> list[RunStep]:
        stmt = (
            select(RunStepRow)
            .where(RunStepRow.run_id == str(run_id))
            .order_by(RunStepRow.position, RunStepRow.id)
        )
        return [run_step_from_row(row) for row in self._session.execute(stmt).scalars()]

    def has_non_terminal_for_run(self, run_id: RunId) -> bool:
        return (
            self._session.execute(
                select(RunStepRow.id)
                .where(
                    RunStepRow.run_id == str(run_id),
                    RunStepRow.status.not_in(
                        tuple(status.value for status in TERMINAL_RUN_STEP_STATUSES)
                    ),
                )
                .limit(1)
            ).first()
            is not None
        )

    def list_for_run_page(
        self, run_id: RunId, limit: int, after_position: int | None, after_id: str | None
    ) -> list[RunStep]:
        stmt = select(RunStepRow).where(RunStepRow.run_id == str(run_id))
        if after_position is not None and after_id is not None:
            stmt = stmt.where(
                or_(
                    RunStepRow.position > after_position,
                    and_(RunStepRow.position == after_position, RunStepRow.id > after_id),
                )
            )
        return [
            run_step_from_row(row)
            for row in self._session.execute(
                stmt.order_by(RunStepRow.position, RunStepRow.id).limit(limit)
            ).scalars()
        ]


class ApprovalRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, approval: ApprovalRequest) -> None:
        self._session.add(approval_to_row(approval))

    def get(self, approval_id: ApprovalRequestId) -> ApprovalRequest | None:
        row = self._session.get(ApprovalRequestRow, str(approval_id))
        return approval_from_row(row) if row is not None else None

    def save(self, approval: ApprovalRequest) -> None:
        self._session.merge(approval_to_row(approval))

    def list_pending_for_run(self, run_id: RunId) -> list[ApprovalRequest]:
        stmt = (
            select(ApprovalRequestRow)
            .where(
                ApprovalRequestRow.run_id == str(run_id),
                ApprovalRequestRow.status == ApprovalStatus.PENDING.value,
            )
            .order_by(ApprovalRequestRow.requested_at, ApprovalRequestRow.id)
        )
        return [approval_from_row(row) for row in self._session.execute(stmt).scalars()]

    def has_pending_for_run(self, run_id: RunId) -> bool:
        return (
            self._session.execute(
                select(ApprovalRequestRow.id)
                .where(
                    ApprovalRequestRow.run_id == str(run_id),
                    ApprovalRequestRow.status == ApprovalStatus.PENDING.value,
                )
                .limit(1)
            ).first()
            is not None
        )

    def list_due_for_expiry(self, now: object, limit: int) -> list[ApprovalRequest]:
        stmt = (
            select(ApprovalRequestRow)
            .where(
                ApprovalRequestRow.status == ApprovalStatus.PENDING.value,
                ApprovalRequestRow.expires_at.is_not(None),
                ApprovalRequestRow.expires_at <= now,
            )
            .order_by(ApprovalRequestRow.requested_at, ApprovalRequestRow.id)
            .limit(limit)
        )
        return [approval_from_row(row) for row in self._session.execute(stmt).scalars()]

    def list_for_run(self, run_id: RunId) -> list[ApprovalRequest]:
        stmt = (
            select(ApprovalRequestRow)
            .where(ApprovalRequestRow.run_id == str(run_id))
            .order_by(ApprovalRequestRow.requested_at, ApprovalRequestRow.id)
        )
        return [approval_from_row(row) for row in self._session.execute(stmt).scalars()]

    def list_recent_for_run(self, run_id: RunId, limit: int) -> list[ApprovalRequest]:
        stmt = (
            select(ApprovalRequestRow)
            .where(ApprovalRequestRow.run_id == str(run_id))
            .order_by(ApprovalRequestRow.requested_at.desc(), ApprovalRequestRow.id.desc())
            .limit(limit)
        )
        rows = list(self._session.execute(stmt).scalars())
        return [approval_from_row(row) for row in reversed(rows)]

    def list_for_run_page(
        self, run_id: RunId, limit: int, after_requested_at: object | None, after_id: str | None
    ) -> list[ApprovalRequest]:
        stmt = select(ApprovalRequestRow).where(ApprovalRequestRow.run_id == str(run_id))
        if after_requested_at is not None and after_id is not None:
            stmt = stmt.where(
                or_(
                    ApprovalRequestRow.requested_at > after_requested_at,
                    and_(
                        ApprovalRequestRow.requested_at == after_requested_at,
                        ApprovalRequestRow.id > after_id,
                    ),
                )
            )
        return [
            approval_from_row(row)
            for row in self._session.execute(
                stmt.order_by(ApprovalRequestRow.requested_at, ApprovalRequestRow.id).limit(limit)
            ).scalars()
        ]


class ArtifactRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, artifact: Artifact) -> None:
        self._session.add(artifact_to_row(artifact))

    def get(self, artifact_id: ArtifactId) -> Artifact | None:
        row = self._session.get(ArtifactRow, str(artifact_id))
        return artifact_from_row(row) if row is not None else None

    def list_for_run(self, run_id: RunId) -> list[Artifact]:
        stmt = (
            select(ArtifactRow)
            .where(ArtifactRow.run_id == str(run_id))
            .order_by(ArtifactRow.created_at, ArtifactRow.id)
        )
        return [artifact_from_row(row) for row in self._session.execute(stmt).scalars()]

    def list_recent_for_run(self, run_id: RunId, limit: int) -> list[Artifact]:
        stmt = (
            select(ArtifactRow)
            .where(ArtifactRow.run_id == str(run_id))
            .order_by(ArtifactRow.created_at.desc(), ArtifactRow.id.desc())
            .limit(limit)
        )
        rows = list(self._session.execute(stmt).scalars())
        return [artifact_from_row(row) for row in reversed(rows)]

    def list_for_run_page(
        self, run_id: RunId, limit: int, after_created_at: object | None, after_id: str | None
    ) -> list[Artifact]:
        stmt = select(ArtifactRow).where(ArtifactRow.run_id == str(run_id))
        if after_created_at is not None and after_id is not None:
            stmt = stmt.where(
                or_(
                    ArtifactRow.created_at > after_created_at,
                    and_(ArtifactRow.created_at == after_created_at, ArtifactRow.id > after_id),
                )
            )
        return [
            artifact_from_row(row)
            for row in self._session.execute(
                stmt.order_by(ArtifactRow.created_at, ArtifactRow.id).limit(limit)
            ).scalars()
        ]


class ToolInvocationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, invocation: ToolInvocation) -> None:
        self._session.add(tool_invocation_to_row(invocation))

    def get(self, invocation_id: ToolInvocationId) -> ToolInvocation | None:
        row = self._session.get(ToolInvocationRow, str(invocation_id))
        return tool_invocation_from_row(row) if row is not None else None

    def save(self, invocation: ToolInvocation) -> None:
        self._session.merge(tool_invocation_to_row(invocation))

    def list_for_run(self, run_id: RunId) -> list[ToolInvocation]:
        stmt = (
            select(ToolInvocationRow)
            .where(ToolInvocationRow.run_id == str(run_id))
            .order_by(ToolInvocationRow.requested_at, ToolInvocationRow.id)
        )
        return [tool_invocation_from_row(row) for row in self._session.execute(stmt).scalars()]

    def has_non_terminal_for_run(self, run_id: RunId) -> bool:
        return (
            self._session.execute(
                select(ToolInvocationRow.id)
                .where(
                    ToolInvocationRow.run_id == str(run_id),
                    ToolInvocationRow.status.not_in(
                        tuple(status.value for status in TERMINAL_TOOL_INVOCATION_STATUSES)
                    ),
                )
                .limit(1)
            ).first()
            is not None
        )

    def list_recent_for_run(self, run_id: RunId, limit: int) -> list[ToolInvocation]:
        stmt = (
            select(ToolInvocationRow)
            .where(ToolInvocationRow.run_id == str(run_id))
            .order_by(ToolInvocationRow.requested_at.desc(), ToolInvocationRow.id.desc())
            .limit(limit)
        )
        rows = list(self._session.execute(stmt).scalars())
        return [tool_invocation_from_row(row) for row in reversed(rows)]

    def list_for_step(self, step_id: RunStepId) -> list[ToolInvocation]:
        stmt = (
            select(ToolInvocationRow)
            .where(ToolInvocationRow.step_id == str(step_id))
            .order_by(ToolInvocationRow.requested_at, ToolInvocationRow.id)
        )
        return [tool_invocation_from_row(row) for row in self._session.execute(stmt).scalars()]

    def _list_page(
        self,
        stmt: Select[tuple[ToolInvocationRow]],
        limit: int,
        after_requested_at: object | None,
        after_id: str | None,
    ) -> list[ToolInvocation]:
        if after_requested_at is not None and after_id is not None:
            stmt = stmt.where(
                or_(
                    ToolInvocationRow.requested_at > after_requested_at,
                    and_(
                        ToolInvocationRow.requested_at == after_requested_at,
                        ToolInvocationRow.id > after_id,
                    ),
                )
            )
        return [
            tool_invocation_from_row(row)
            for row in self._session.execute(
                stmt.order_by(ToolInvocationRow.requested_at, ToolInvocationRow.id).limit(limit)
            ).scalars()
        ]

    def list_for_run_page(
        self, run_id: RunId, limit: int, after_requested_at: object | None, after_id: str | None
    ) -> list[ToolInvocation]:
        return self._list_page(
            select(ToolInvocationRow).where(ToolInvocationRow.run_id == str(run_id)),
            limit,
            after_requested_at,
            after_id,
        )

    def list_for_step_page(
        self,
        step_id: RunStepId,
        limit: int,
        after_requested_at: object | None,
        after_id: str | None,
    ) -> list[ToolInvocation]:
        return self._list_page(
            select(ToolInvocationRow).where(ToolInvocationRow.step_id == str(step_id)),
            limit,
            after_requested_at,
            after_id,
        )


class RunEventStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, event: RunEvent) -> None:
        self._session.add(run_event_to_row(event))

    def latest_of_type_for_run(self, run_id: RunId, event_type: RunEventType) -> RunEvent | None:
        stmt = (
            select(RunEventRow)
            .where(RunEventRow.run_id == str(run_id), RunEventRow.type == event_type.value)
            .order_by(RunEventRow.sequence.desc())
            .limit(1)
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        return run_event_from_row(row) if row is not None else None

    def list_for_run(self, run_id: RunId) -> list[RunEvent]:
        stmt = (
            select(RunEventRow)
            .where(RunEventRow.run_id == str(run_id))
            .order_by(RunEventRow.sequence)
        )
        return [run_event_from_row(row) for row in self._session.execute(stmt).scalars()]

    def list_recent_for_run(self, run_id: RunId, limit: int) -> list[RunEvent]:
        stmt = (
            select(RunEventRow)
            .where(RunEventRow.run_id == str(run_id))
            .order_by(RunEventRow.sequence.desc())
            .limit(limit)
        )
        rows = list(self._session.execute(stmt).scalars())
        return [run_event_from_row(row) for row in reversed(rows)]

    def list_after_sequence(self, run_id: RunId, after_sequence: int, limit: int) -> list[RunEvent]:
        stmt = (
            select(RunEventRow)
            .where(RunEventRow.run_id == str(run_id), RunEventRow.sequence > after_sequence)
            .order_by(RunEventRow.sequence)
            .limit(limit)
        )
        return [run_event_from_row(row) for row in self._session.execute(stmt).scalars()]

    def reserve_sequences(self, run_id: RunId, count: int) -> int:
        self._session.execute(
            insert(RunEventSequenceCounterRow)
            .values(run_id=str(run_id), next_value=1)
            .on_conflict_do_nothing(index_elements=[RunEventSequenceCounterRow.run_id])
        )
        stmt = (
            update(RunEventSequenceCounterRow)
            .where(RunEventSequenceCounterRow.run_id == str(run_id))
            .values(next_value=RunEventSequenceCounterRow.next_value + count)
            .returning(RunEventSequenceCounterRow.next_value)
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], self._session.execute(stmt))
        post_increment_value = cast(int, result.scalar_one())
        return post_increment_value - count


class TaskEventStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, event: TaskEvent) -> None:
        self._session.add(task_event_to_row(event))

    def reserve_sequences(self, task_id: TaskId, count: int) -> int:
        self._session.execute(
            insert(TaskEventSequenceCounterRow)
            .values(task_id=str(task_id), next_value=1)
            .on_conflict_do_nothing(index_elements=[TaskEventSequenceCounterRow.task_id])
        )
        stmt = (
            update(TaskEventSequenceCounterRow)
            .where(TaskEventSequenceCounterRow.task_id == str(task_id))
            .values(next_value=TaskEventSequenceCounterRow.next_value + count)
            .returning(TaskEventSequenceCounterRow.next_value)
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], self._session.execute(stmt))
        post_increment_value = cast(int, result.scalar_one())
        return post_increment_value - count

    def list_for_task(self, task_id: TaskId) -> list[TaskEvent]:
        stmt = (
            select(TaskEventRow)
            .where(TaskEventRow.task_id == str(task_id))
            .order_by(TaskEventRow.sequence)
        )
        return [task_event_from_row(row) for row in self._session.execute(stmt).scalars()]

    def list_after_sequence(
        self, task_id: TaskId, after_sequence: int, limit: int
    ) -> list[TaskEvent]:
        stmt = (
            select(TaskEventRow)
            .where(TaskEventRow.task_id == str(task_id), TaskEventRow.sequence > after_sequence)
            .order_by(TaskEventRow.sequence)
            .limit(limit)
        )
        return [task_event_from_row(row) for row in self._session.execute(stmt).scalars()]


_MAX_MEMORY_RETRIEVAL_ITEMS_PER_RECORD = 50


class MemoryIndexSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, snapshot: IndexSnapshot) -> None:
        self._session.add(index_snapshot_to_row(snapshot))

    def latest(self) -> IndexSnapshot | None:
        stmt = (
            select(MemoryIndexSnapshotRow)
            .order_by(MemoryIndexSnapshotRow.built_at.desc(), MemoryIndexSnapshotRow.id.desc())
            .limit(1)
        )
        row = self._session.execute(stmt).scalars().first()
        return index_snapshot_from_row(row) if row is not None else None

    def mark_stale(self, snapshot_id: str) -> None:
        self._session.execute(
            update(MemoryIndexSnapshotRow)
            .where(MemoryIndexSnapshotRow.id == snapshot_id)
            .values(status=IndexState.STALE.value)
        )


class MemoryRetrievalRecordRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: MemoryRetrievalRecord) -> None:
        if len(record.items) > _MAX_MEMORY_RETRIEVAL_ITEMS_PER_RECORD:
            raise ValueError(
                f"MemoryRetrievalRecord.items exceeds the {_MAX_MEMORY_RETRIEVAL_ITEMS_PER_RECORD} "
                "item cap per record"
            )
        self._session.add(memory_retrieval_record_to_row(record))
        # No ORM relationship links record and item rows, so SQLAlchemy's
        # flush-time dependency sort has no way to order the parent insert
        # before the children's -- force it explicitly or a real commit (as
        # opposed to a mid-transaction flush a caller never commits) can
        # attempt the item inserts first and fail the record_id FK.
        self._session.flush()
        for item in record.items:
            self._session.add(memory_retrieval_item_to_row(item, record_id=record.id))

    def get(self, record_id: str) -> MemoryRetrievalRecord | None:
        row = self._session.get(MemoryRetrievalRecordRow, record_id)
        if row is None:
            return None
        item_stmt = (
            select(MemoryRetrievalItemRow)
            .where(MemoryRetrievalItemRow.record_id == record_id)
            .order_by(MemoryRetrievalItemRow.rank)
        )
        items = tuple(
            memory_retrieval_item_from_row(item_row)
            for item_row in self._session.execute(item_stmt).scalars()
        )
        return memory_retrieval_record_from_row(row, items)
