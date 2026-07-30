from __future__ import annotations

import builtins
from datetime import datetime
from typing import Any, cast

from sqlalchemy import DateTime, Select, String, and_, exists, func, literal, or_, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from friday.application.memory.models import IndexSnapshot, IndexState, MemoryRetrievalRecord
from friday.application.ports import validate_delivery_attempt_history_limit
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
    DeliveryAttempt,
    DeliveryAttemptId,
    DeliveryAttemptOutcome,
    DeliveryId,
    DeliveryStatus,
    OutboundDelivery,
    Run,
    RunEvent,
    RunEventType,
    RunId,
    RunStep,
    RunStepId,
    Schedule,
    ScheduleDeliveryPolicy,
    ScheduleFire,
    ScheduleFireDeliveryPlan,
    ScheduleFireId,
    ScheduleId,
    Task,
    TaskEvent,
    TaskId,
    ToolInvocation,
    ToolInvocationId,
)
from friday.domain.delivery_attempt import validate_delivery_attempt_shape
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
    delivery_attempt_from_row,
    index_snapshot_from_row,
    index_snapshot_to_row,
    memory_retrieval_item_from_row,
    memory_retrieval_item_to_row,
    memory_retrieval_record_from_row,
    memory_retrieval_record_to_row,
    outbound_delivery_from_row,
    outbound_delivery_to_row,
    read_back_utc,
    run_event_from_row,
    run_event_to_row,
    run_from_row,
    run_step_from_row,
    run_step_to_row,
    run_to_row,
    schedule_delivery_policy_from_row,
    schedule_delivery_policy_to_row,
    schedule_fire_delivery_plan_from_row,
    schedule_fire_delivery_plan_to_row,
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
    DeliveryAttemptRow,
    MemoryIndexSnapshotRow,
    MemoryRetrievalItemRow,
    MemoryRetrievalRecordRow,
    OutboundDeliveryRow,
    RunEventRow,
    RunEventSequenceCounterRow,
    RunRow,
    RunStepRow,
    ScheduleDeliveryPolicyRow,
    ScheduleFireDeliveryPlanRow,
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


class ScheduleDeliveryPolicyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_for_schedule(self, schedule_id: ScheduleId) -> ScheduleDeliveryPolicy | None:
        row = self._session.get(ScheduleDeliveryPolicyRow, str(schedule_id))
        return schedule_delivery_policy_from_row(row) if row is not None else None

    def put_for_nonterminal_schedule(self, policy: ScheduleDeliveryPolicy) -> bool:
        """Persist policy only while its schedule is durably non-terminal.

        This is intentionally the only production policy write primitive: a
        generic merge would let callers persist authority after a stale status
        read. The predicate is evaluated by the same SQL statement.
        """
        row = schedule_delivery_policy_to_row(policy)
        values = {
            "schedule_id": row.schedule_id,
            "route_id": row.route_id,
            "enabled": row.enabled,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        nonterminal = exists(
            select(ScheduleRow.id).where(
                ScheduleRow.id == str(policy.schedule_id),
                ScheduleRow.status.not_in(
                    (ScheduleStatus.COMPLETED.value, ScheduleStatus.CANCELLED.value)
                ),
            )
        )
        stmt = (
            insert(ScheduleDeliveryPolicyRow)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[ScheduleDeliveryPolicyRow.schedule_id],
                set_={
                    "route_id": row.route_id,
                    "enabled": row.enabled,
                    "updated_at": row.updated_at,
                },
                where=nonterminal,
            )
        )
        # SQLite's UPSERT does not predicate the insert arm. Guard both arms
        # with an INSERT .. SELECT so a terminal schedule cannot gain a row.
        existing = self._session.get(ScheduleDeliveryPolicyRow, str(policy.schedule_id))
        if existing is None:
            if not self._session.execute(select(nonterminal)).scalar_one():
                return False
            self._session.add(row)
            return True
        result = cast(CursorResult[Any], self._session.execute(stmt))
        return result.rowcount == 1


class ScheduleFireDeliveryPlanRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_for_fire(self, plan: ScheduleFireDeliveryPlan, fire: ScheduleFire) -> None:
        if (
            plan.schedule_fire_id != fire.id
            or plan.schedule_id != fire.schedule_id
            or plan.execution_id != fire.run_id
        ):
            raise ValueError("delivery plan must bind exactly to its ScheduleFire")
        self._session.add(schedule_fire_delivery_plan_to_row(plan))

    def get_by_fire(self, schedule_fire_id: ScheduleFireId) -> ScheduleFireDeliveryPlan | None:
        row = self._session.execute(
            select(ScheduleFireDeliveryPlanRow).where(
                ScheduleFireDeliveryPlanRow.schedule_fire_id == str(schedule_fire_id)
            )
        ).scalar_one_or_none()
        return schedule_fire_delivery_plan_from_row(row) if row is not None else None


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


class OutboundDeliveryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, delivery: OutboundDelivery) -> None:
        self._session.add(outbound_delivery_to_row(delivery))

    def get(self, delivery_id: DeliveryId) -> OutboundDelivery | None:
        row = self._session.get(OutboundDeliveryRow, str(delivery_id))
        return outbound_delivery_from_row(row) if row is not None else None

    def get_by_source_tool_invocation_id(
        self, invocation_id: ToolInvocationId
    ) -> OutboundDelivery | None:
        row = self._session.execute(
            select(OutboundDeliveryRow).where(
                OutboundDeliveryRow.source_tool_invocation_id == str(invocation_id)
            )
        ).scalar_one_or_none()
        return outbound_delivery_from_row(row) if row is not None else None

    def list_due(self, now: object, limit: int) -> list[OutboundDelivery]:
        stmt = (
            select(OutboundDeliveryRow)
            .where(
                OutboundDeliveryRow.status == DeliveryStatus.QUEUED.value,
                OutboundDeliveryRow.available_at <= now,
            )
            .order_by(OutboundDeliveryRow.available_at, OutboundDeliveryRow.id)
            .limit(limit)
        )
        return [outbound_delivery_from_row(row) for row in self._session.execute(stmt).scalars()]

    def find_expired_claims(self, now: object, limit: int) -> list[OutboundDelivery]:
        stmt = (
            select(OutboundDeliveryRow)
            .where(
                OutboundDeliveryRow.status == DeliveryStatus.SENDING.value,
                OutboundDeliveryRow.claim_expires_at.is_not(None),
                OutboundDeliveryRow.claim_expires_at <= now,
            )
            .order_by(OutboundDeliveryRow.claim_expires_at, OutboundDeliveryRow.id)
            .limit(limit)
        )
        return [outbound_delivery_from_row(row) for row in self._session.execute(stmt).scalars()]

    def try_claim(
        self,
        delivery_id: DeliveryId,
        worker_id: str,
        claim_token: str,
        now: object,
        lease_expires_at: object,
    ) -> int | None:
        """Atomically claim one due QUEUED delivery; return its new generation.

        Claiming is a single fenced UPDATE, never read-modify-write: two
        workers racing on the same row produce exactly one `rowcount == 1`.
        """
        stmt = (
            update(OutboundDeliveryRow)
            .where(
                OutboundDeliveryRow.id == str(delivery_id),
                OutboundDeliveryRow.status == DeliveryStatus.QUEUED.value,
                OutboundDeliveryRow.available_at <= now,
            )
            .values(
                status=DeliveryStatus.SENDING.value,
                claim_owner=worker_id,
                claim_token=claim_token,
                claim_generation=OutboundDeliveryRow.claim_generation + 1,
                claim_expires_at=lease_expires_at,
                attempt_count=OutboundDeliveryRow.attempt_count + 1,
                updated_at=now,
            )
            .returning(OutboundDeliveryRow.claim_generation)
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], self._session.execute(stmt))
        generations = result.scalars().all()
        return cast(int, generations[0]) if len(generations) == 1 else None

    def _active_claim_predicate(
        self,
        delivery_id: DeliveryId,
        worker_id: str,
        claim_token: str,
        generation: int,
        now: object,
    ) -> tuple[Any, ...]:
        return (
            OutboundDeliveryRow.id == str(delivery_id),
            OutboundDeliveryRow.status == DeliveryStatus.SENDING.value,
            OutboundDeliveryRow.claim_owner == worker_id,
            OutboundDeliveryRow.claim_token == claim_token,
            OutboundDeliveryRow.claim_generation == generation,
            OutboundDeliveryRow.claim_expires_at.is_not(None),
            OutboundDeliveryRow.claim_expires_at > now,
        )

    def is_claim_active(
        self,
        delivery_id: DeliveryId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: object,
    ) -> bool:
        stmt = select(func.count()).where(
            *self._active_claim_predicate(
                delivery_id, worker_id, claim_token, claim_generation, now
            )
        )
        return bool(self._session.execute(stmt).scalar_one())

    def mark_dispatch_started(
        self,
        delivery_id: DeliveryId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: object,
    ) -> bool:
        """Durably cross the external side-effect boundary, once, under claim."""
        stmt = (
            update(OutboundDeliveryRow)
            .where(
                *self._active_claim_predicate(
                    delivery_id, worker_id, claim_token, claim_generation, now
                ),
                OutboundDeliveryRow.dispatch_started_at.is_(None),
            )
            .values(dispatch_started_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], self._session.execute(stmt))
        return result.rowcount == 1

    def save_claimed_lifecycle(
        self,
        delivery: OutboundDelivery,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: object,
    ) -> bool:
        """Persist lifecycle state only, fenced by an exact unexpired claim.

        Authority and content columns (route binding, subject, body, digest,
        source identity) are never in the SET clause, so no worker — current
        or stale — can retarget a delivery through an outcome write.
        `attempt_count`, `claim_generation` and `dispatch_started_at` are
        excluded too: they change only via `try_claim` and
        `mark_dispatch_started`.

        A requeue to QUEUED additionally requires `dispatch_started_at IS
        NULL`: this is the only fenced write that can move a delivery out of
        SENDING pre-dispatch, so it must not be able to smuggle a requeue
        through once the external side-effect boundary has been crossed.
        """
        extra_predicate = (
            (OutboundDeliveryRow.dispatch_started_at.is_(None),)
            if delivery.status is DeliveryStatus.QUEUED
            else ()
        )
        stmt = (
            update(OutboundDeliveryRow)
            .where(
                *self._active_claim_predicate(
                    delivery_id=delivery.id,
                    worker_id=worker_id,
                    claim_token=claim_token,
                    generation=claim_generation,
                    now=now,
                ),
                *extra_predicate,
            )
            .values(
                status=delivery.status.value,
                available_at=delivery.available_at,
                claim_owner=delivery.claim_owner,
                claim_token=delivery.claim_token,
                claim_expires_at=delivery.claim_expires_at,
                provider_message_id=delivery.provider_message_id,
                failure_code=delivery.failure_code,
                failure_message=delivery.failure_message,
                delivered_at=delivery.delivered_at,
                updated_at=delivery.updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], self._session.execute(stmt))
        return result.rowcount == 1

    def _expired_claim_predicate(
        self, delivery_id: DeliveryId, claim_generation: int, now: object
    ) -> tuple[Any, ...]:
        return (
            OutboundDeliveryRow.id == str(delivery_id),
            OutboundDeliveryRow.status == DeliveryStatus.SENDING.value,
            OutboundDeliveryRow.claim_generation == claim_generation,
            OutboundDeliveryRow.claim_expires_at.is_not(None),
            OutboundDeliveryRow.claim_expires_at <= now,
        )

    def requeue_expired_pre_dispatch(
        self, delivery_id: DeliveryId, claim_generation: int, now: object, available_at: object
    ) -> bool:
        """Requeue an expired claim whose dispatch boundary was never crossed.

        `dispatch_started_at IS NULL` is the whole safety argument for this
        transition: no external message can exist yet, so a retry cannot
        duplicate one. `attempt_count` and `claim_generation` are preserved.
        """
        stmt = (
            update(OutboundDeliveryRow)
            .where(
                *self._expired_claim_predicate(delivery_id, claim_generation, now),
                OutboundDeliveryRow.dispatch_started_at.is_(None),
            )
            .values(
                status=DeliveryStatus.QUEUED.value,
                available_at=available_at,
                claim_owner=None,
                claim_token=None,
                claim_expires_at=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], self._session.execute(stmt))
        return result.rowcount == 1

    def fail_expired_pre_dispatch(
        self,
        delivery_id: DeliveryId,
        claim_generation: int,
        now: object,
        failure_code: str,
        failure_message: str,
    ) -> bool:
        """Terminally fail an expired pre-dispatch claim with no retry budget."""
        stmt = (
            update(OutboundDeliveryRow)
            .where(
                *self._expired_claim_predicate(delivery_id, claim_generation, now),
                OutboundDeliveryRow.dispatch_started_at.is_(None),
            )
            .values(
                status=DeliveryStatus.FAILED.value,
                claim_owner=None,
                claim_token=None,
                claim_expires_at=None,
                failure_code=failure_code,
                failure_message=failure_message,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], self._session.execute(stmt))
        return result.rowcount == 1

    def mark_expired_post_dispatch_ambiguous(
        self,
        delivery_id: DeliveryId,
        claim_generation: int,
        now: object,
        failure_code: str,
        failure_message: str,
    ) -> bool:
        """Park an expired claim that may already have sent an external message.

        `dispatch_started_at IS NOT NULL` means the side effect cannot be
        ruled out, so this is terminal: never requeued automatically.
        """
        stmt = (
            update(OutboundDeliveryRow)
            .where(
                *self._expired_claim_predicate(delivery_id, claim_generation, now),
                OutboundDeliveryRow.dispatch_started_at.is_not(None),
            )
            .values(
                status=DeliveryStatus.AMBIGUOUS.value,
                claim_owner=None,
                claim_token=None,
                claim_expires_at=None,
                failure_code=failure_code,
                failure_message=failure_message,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], self._session.execute(stmt))
        return result.rowcount == 1


class DeliveryAttemptRepository:
    """Claim-fenced audit ledger writes; no generic row insert or save exists.

    Every write here is a single fenced statement whose WHERE clause proves,
    inside the database, that the caller still owns the exact claim it thinks
    it owns. There is deliberately no `add()`: a durable attempt row must not
    be creatable without an active claim that crossed the dispatch boundary,
    so the only insert path is `begin_for_claim`.

    Terminal writes reuse the domain's `validate_delivery_attempt_shape`
    before touching SQL, so a direct repository call cannot persist a
    lifecycle shape `DeliveryAttempt.complete()` would have rejected.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def begin_for_claim(
        self,
        attempt_id: DeliveryAttemptId,
        delivery_id: DeliveryId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        started_at: object,
        now: object,
    ) -> bool:
        """Insert exactly one IN_PROGRESS attempt, fenced on an active claim.

        `INSERT ... SELECT` rather than a plain insert: the row only
        materializes if the SELECT still finds a SENDING delivery owned by
        this exact (worker, token, generation) with an unexpired lease whose
        `dispatch_started_at` already equals this boundary. A stale, expired,
        unclaimed, or not-yet-dispatched delivery selects zero rows, so no
        attempt is written and the caller learns it lost the claim.

        `UNIQUE(delivery_id, claim_generation)` remains the race fence: two
        concurrent begins for one generation cannot both commit.
        """
        source = select(
            literal(str(attempt_id)),
            OutboundDeliveryRow.id,
            OutboundDeliveryRow.claim_generation,
            literal(started_at, DateTime),
            literal(None, DateTime),
            literal(DeliveryAttemptOutcome.IN_PROGRESS.value),
            literal(None, String),
        ).where(
            OutboundDeliveryRow.id == str(delivery_id),
            OutboundDeliveryRow.status == DeliveryStatus.SENDING.value,
            OutboundDeliveryRow.claim_owner == worker_id,
            OutboundDeliveryRow.claim_token == claim_token,
            OutboundDeliveryRow.claim_generation == claim_generation,
            OutboundDeliveryRow.claim_expires_at.is_not(None),
            OutboundDeliveryRow.claim_expires_at > now,
            # The boundary for *this* generation must already be durable.
            OutboundDeliveryRow.dispatch_started_at.is_not(None),
            OutboundDeliveryRow.dispatch_started_at == started_at,
        )
        stmt = insert(DeliveryAttemptRow).from_select(
            [
                "id",
                "delivery_id",
                "claim_generation",
                "started_at",
                "finished_at",
                "outcome",
                "failure_code",
            ],
            source,
        )
        return cast(CursorResult[Any], self._session.execute(stmt)).rowcount == 1

    def get_for_generation(
        self, delivery_id: DeliveryId, claim_generation: int
    ) -> DeliveryAttempt | None:
        row = self._session.execute(
            select(DeliveryAttemptRow).where(
                DeliveryAttemptRow.delivery_id == str(delivery_id),
                DeliveryAttemptRow.claim_generation == claim_generation,
            )
        ).scalar_one_or_none()
        return delivery_attempt_from_row(row) if row is not None else None

    def list_for_delivery(self, delivery_id: DeliveryId, limit: int) -> list[DeliveryAttempt]:
        """Read at most `limit` attempts, newest boundary crossing first.

        `limit` is validated rather than passed through: SQLite treats a
        negative LIMIT as unbounded, so an unchecked value would silently turn
        a bounded audit read into a full-table scan.
        """
        validate_delivery_attempt_history_limit(limit)
        rows = self._session.execute(
            select(DeliveryAttemptRow)
            .where(DeliveryAttemptRow.delivery_id == str(delivery_id))
            .order_by(DeliveryAttemptRow.started_at.desc(), DeliveryAttemptRow.id.desc())
            .limit(limit)
        ).scalars()
        return [delivery_attempt_from_row(row) for row in rows]

    @staticmethod
    def _active_delivery_exists(
        delivery_id: DeliveryId, worker_id: str, claim_token: str, generation: int, now: object
    ) -> Any:
        return exists(
            select(1)
            .select_from(OutboundDeliveryRow)
            .where(
                OutboundDeliveryRow.id == str(delivery_id),
                OutboundDeliveryRow.status == DeliveryStatus.SENDING.value,
                OutboundDeliveryRow.claim_owner == worker_id,
                OutboundDeliveryRow.claim_token == claim_token,
                OutboundDeliveryRow.claim_generation == generation,
                OutboundDeliveryRow.claim_expires_at.is_not(None),
                OutboundDeliveryRow.claim_expires_at > now,
            )
        )

    def _validated_terminal_write(
        self,
        delivery_id: DeliveryId,
        claim_generation: int,
        now: object,
        outcome: DeliveryAttemptOutcome,
        failure_code: str | None,
    ) -> str | None:
        """Reject a terminal shape the domain would reject, before any SQL.

        The row's own `started_at` is read back so `finished_at >= started_at`
        is checked against the persisted boundary rather than against a
        caller's claim about it. When there is no closeable IN_PROGRESS row the
        ordering rule has nothing to compare against, so it degenerates while
        every outcome/failure-code rule still applies — the fenced UPDATE that
        follows matches zero rows regardless.
        """
        finished_at = cast(datetime, now)
        started_at = self._session.execute(
            select(DeliveryAttemptRow.started_at).where(
                DeliveryAttemptRow.delivery_id == str(delivery_id),
                DeliveryAttemptRow.claim_generation == claim_generation,
                DeliveryAttemptRow.outcome == DeliveryAttemptOutcome.IN_PROGRESS.value,
            )
        ).scalar_one_or_none()
        return validate_delivery_attempt_shape(
            outcome=outcome,
            started_at=read_back_utc(started_at) if started_at is not None else finished_at,
            finished_at=finished_at,
            failure_code=failure_code,
        )

    def complete_for_claim(
        self,
        delivery_id: DeliveryId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: object,
        outcome: DeliveryAttemptOutcome,
        failure_code: str | None,
    ) -> bool:
        code = self._validated_terminal_write(
            delivery_id, claim_generation, now, outcome, failure_code
        )
        stmt = (
            update(DeliveryAttemptRow)
            .where(
                DeliveryAttemptRow.delivery_id == str(delivery_id),
                DeliveryAttemptRow.claim_generation == claim_generation,
                DeliveryAttemptRow.outcome == DeliveryAttemptOutcome.IN_PROGRESS.value,
                self._active_delivery_exists(
                    delivery_id, worker_id, claim_token, claim_generation, now
                ),
            )
            .values(outcome=outcome.value, finished_at=now, failure_code=code)
            .execution_options(synchronize_session=False)
        )
        return cast(CursorResult[Any], self._session.execute(stmt)).rowcount == 1

    def close_expired_as_ambiguous(
        self, delivery_id: DeliveryId, claim_generation: int, now: object, failure_code: str
    ) -> bool:
        code = self._validated_terminal_write(
            delivery_id,
            claim_generation,
            now,
            DeliveryAttemptOutcome.AMBIGUOUS,
            failure_code,
        )
        expired = exists(
            select(1)
            .select_from(OutboundDeliveryRow)
            .where(
                OutboundDeliveryRow.id == str(delivery_id),
                OutboundDeliveryRow.status == DeliveryStatus.SENDING.value,
                OutboundDeliveryRow.claim_generation == claim_generation,
                OutboundDeliveryRow.claim_expires_at.is_not(None),
                OutboundDeliveryRow.claim_expires_at <= now,
                OutboundDeliveryRow.dispatch_started_at.is_not(None),
            )
        )
        stmt = (
            update(DeliveryAttemptRow)
            .where(
                DeliveryAttemptRow.delivery_id == str(delivery_id),
                DeliveryAttemptRow.claim_generation == claim_generation,
                DeliveryAttemptRow.outcome == DeliveryAttemptOutcome.IN_PROGRESS.value,
                expired,
            )
            .values(
                outcome=DeliveryAttemptOutcome.AMBIGUOUS.value,
                finished_at=now,
                failure_code=code,
            )
            .execution_options(synchronize_session=False)
        )
        return cast(CursorResult[Any], self._session.execute(stmt)).rowcount == 1


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
