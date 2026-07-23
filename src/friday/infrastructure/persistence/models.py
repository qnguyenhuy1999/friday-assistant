from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TaskRow(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(primary_key=True)
    title: Mapped[str]
    description: Mapped[str]
    status: Mapped[str]
    created_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    failed_at: Mapped[datetime | None]
    cancelled_at: Mapped[datetime | None]
    failure: Mapped[dict[str, object] | None] = mapped_column(JSON)


class RunRow(Base):
    __tablename__ = "runs"
    __table_args__ = (Index("ix_runs_task_id", "task_id"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    status: Mapped[str]
    created_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    failure: Mapped[dict[str, object] | None] = mapped_column(JSON)
    # No DB-level FK to approval_requests: see docs/architecture/persistence.md
    # ("Cross-reference columns without FK constraints").
    approval_request_id: Mapped[str | None] = mapped_column(index=True)


class RunStepRow(Base):
    __tablename__ = "run_steps"
    __table_args__ = (
        Index("ix_run_steps_run_id", "run_id"),
        UniqueConstraint("run_id", "position", name="uq_run_steps_run_id_position"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    name: Mapped[str]
    position: Mapped[int]
    status: Mapped[str]
    created_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    failure: Mapped[dict[str, object] | None] = mapped_column(JSON)
    approval_request_id: Mapped[str | None] = mapped_column(index=True)


class ApprovalRequestRow(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (Index("ix_approval_requests_run_id", "run_id"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    step_id: Mapped[str | None] = mapped_column(ForeignKey("run_steps.id"))
    category: Mapped[str]
    summary: Mapped[str]
    reason: Mapped[str]
    requested_action: Mapped[str]
    requested_input: Mapped[object | None] = mapped_column(JSON)
    status: Mapped[str]
    requested_at: Mapped[datetime]
    resolved_at: Mapped[datetime | None]
    resolution_note: Mapped[str | None]
    resolver: Mapped[str | None]


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_run_id", "run_id"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    step_id: Mapped[str | None] = mapped_column(ForeignKey("run_steps.id"))
    kind: Mapped[str]
    name: Mapped[str]
    media_type: Mapped[str]
    location: Mapped[str]
    created_at: Mapped[datetime]
    size: Mapped[int | None]
    checksum: Mapped[str | None]
    artifact_metadata: Mapped[object | None] = mapped_column("metadata", JSON)


class ToolInvocationRow(Base):
    __tablename__ = "tool_invocations"
    __table_args__ = (Index("ix_tool_invocations_run_id", "run_id"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    step_id: Mapped[str | None] = mapped_column(ForeignKey("run_steps.id"))
    approval_request_id: Mapped[str | None] = mapped_column(index=True)
    tool_name: Mapped[str]
    requested_input: Mapped[object | None] = mapped_column(JSON)
    status: Mapped[str]
    requested_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    output: Mapped[object | None] = mapped_column(JSON)
    output_set: Mapped[bool]
    failure: Mapped[dict[str, object] | None] = mapped_column(JSON)


class RunEventRow(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        Index("ix_run_events_run_id", "run_id"),
        UniqueConstraint("run_id", "sequence", name="uq_run_events_run_id_sequence"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    step_id: Mapped[str | None] = mapped_column(ForeignKey("run_steps.id"))
    type: Mapped[str]
    sequence: Mapped[int]
    occurred_at: Mapped[datetime]
    payload: Mapped[object | None] = mapped_column(JSON)
