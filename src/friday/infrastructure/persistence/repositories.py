from __future__ import annotations

import builtins

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from friday.domain import (
    ApprovalRequest,
    ApprovalRequestId,
    ApprovalStatus,
    Artifact,
    ArtifactId,
    Run,
    RunEvent,
    RunId,
    RunStep,
    RunStepId,
    Task,
    TaskEvent,
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
    RunEventRow,
    RunRow,
    RunStepRow,
    TaskEventRow,
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

    def list_for_run(self, run_id: RunId) -> list[ApprovalRequest]:
        stmt = (
            select(ApprovalRequestRow)
            .where(ApprovalRequestRow.run_id == str(run_id))
            .order_by(ApprovalRequestRow.requested_at, ApprovalRequestRow.id)
        )
        return [approval_from_row(row) for row in self._session.execute(stmt).scalars()]

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

    def list_for_run(self, run_id: RunId) -> list[RunEvent]:
        stmt = (
            select(RunEventRow)
            .where(RunEventRow.run_id == str(run_id))
            .order_by(RunEventRow.sequence)
        )
        return [run_event_from_row(row) for row in self._session.execute(stmt).scalars()]

    def list_after_sequence(self, run_id: RunId, after_sequence: int, limit: int) -> list[RunEvent]:
        stmt = (
            select(RunEventRow)
            .where(RunEventRow.run_id == str(run_id), RunEventRow.sequence > after_sequence)
            .order_by(RunEventRow.sequence)
            .limit(limit)
        )
        return [run_event_from_row(row) for row in self._session.execute(stmt).scalars()]

    def next_sequence(self, run_id: RunId) -> int:
        stmt = select(func.max(RunEventRow.sequence)).where(RunEventRow.run_id == str(run_id))
        current_max = self._session.execute(stmt).scalar()
        return (current_max or 0) + 1


class TaskEventStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, event: TaskEvent) -> None:
        self._session.add(task_event_to_row(event))

    def next_sequence(self, task_id: TaskId) -> int:
        stmt = select(func.max(TaskEventRow.sequence)).where(TaskEventRow.task_id == str(task_id))
        return (self._session.execute(stmt).scalar() or 0) + 1

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
