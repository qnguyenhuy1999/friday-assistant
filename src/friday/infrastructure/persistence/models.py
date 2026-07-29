from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, UniqueConstraint
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


class TaskEventRow(Base):
    __tablename__ = "task_events"
    __table_args__ = (
        Index("ix_task_events_task_id", "task_id"),
        UniqueConstraint("task_id", "sequence", name="uq_task_events_task_id_sequence"),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    type: Mapped[str]
    sequence: Mapped[int]
    occurred_at: Mapped[datetime]
    payload: Mapped[object | None] = mapped_column(JSON)


class TaskEventSequenceCounterRow(Base):
    __tablename__ = "task_event_sequence_counters"

    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    next_value: Mapped[int]


class RunRow(Base):
    __tablename__ = "runs"
    __table_args__ = (Index("ix_runs_task_id", "task_id"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    execution_id: Mapped[str] = mapped_column(index=True)
    status: Mapped[str]
    created_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    failure: Mapped[dict[str, object] | None] = mapped_column(JSON)
    # No DB-level FK to approval_requests: see docs/architecture/persistence.md
    # ("Cross-reference columns without FK constraints").
    approval_request_id: Mapped[str | None] = mapped_column(index=True)


class ScheduleRow(Base):
    __tablename__ = "schedules"
    __table_args__ = (
        Index("ix_schedules_due", "status", "next_fire_at"),
        CheckConstraint(
            "status IN ('active', 'paused', 'completed', 'cancelled')", name="ck_schedules_status"
        ),
        CheckConstraint("kind IN ('once', 'cron')", name="ck_schedules_kind"),
        CheckConstraint(
            "(kind = 'once' AND run_at IS NOT NULL AND cron IS NULL) OR "
            "(kind = 'cron' AND cron IS NOT NULL AND run_at IS NULL)",
            name="ck_schedules_kind_shape",
        ),
        CheckConstraint(
            "(status = 'active' AND next_fire_at IS NOT NULL) OR (status = 'paused') OR "
            "(status IN ('completed', 'cancelled') AND next_fire_at IS NULL)",
            name="ck_schedules_status_next_fire",
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    kind: Mapped[str]
    cron: Mapped[str | None]
    run_at: Mapped[datetime | None]
    timezone: Mapped[str]
    status: Mapped[str]
    next_fire_at: Mapped[datetime | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class ScheduleFireRow(Base):
    __tablename__ = "schedule_fires"
    __table_args__ = (
        UniqueConstraint("schedule_id", "scheduled_for", name="uq_schedule_fires_occurrence"),
        UniqueConstraint("run_id", name="uq_schedule_fires_run_id"),
        Index("ix_schedule_fires_schedule_id", "schedule_id"),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    schedule_id: Mapped[str] = mapped_column(ForeignKey("schedules.id"))
    scheduled_for: Mapped[datetime]
    fired_at: Mapped[datetime]
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))


class ConversationRow(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(primary_key=True)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class ConversationTurnRow(Base):
    __tablename__ = "conversation_turns"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "client_turn_id", name="uq_conversation_turns_client_turn_id"
        ),
        UniqueConstraint("run_id", name="uq_conversation_turns_run_id"),
        Index("ix_conversation_turns_conversation_id", "conversation_id"),
        Index(
            "ix_conversation_turns_conversation_id_created_at_id",
            "conversation_id",
            "created_at",
            "id",
        ),
        CheckConstraint(
            "input_mode IN ('typed', 'push_to_talk', 'hands_free')",
            name="ck_conversation_turns_input_mode",
        ),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    client_turn_id: Mapped[str]
    input_text: Mapped[str]
    input_mode: Mapped[str]
    recognition_language: Mapped[str | None]
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    created_at: Mapped[datetime]


class RunWorkItemRow(Base):
    __tablename__ = "run_work_items"
    __table_args__ = (
        Index("ix_run_work_items_available_at", "available_at"),
        Index("ix_run_work_items_lease_expires_at", "lease_expires_at"),
        Index(
            "ix_run_work_items_available_at_enqueued_at_run_id",
            "available_at",
            "enqueued_at",
            "run_id",
        ),
    )

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    available_at: Mapped[datetime]
    enqueued_at: Mapped[datetime]
    claimed_by: Mapped[str | None]
    claim_token: Mapped[str | None]
    claim_generation: Mapped[int] = mapped_column(default=0, server_default="0")
    claimed_at: Mapped[datetime | None]
    heartbeat_at: Mapped[datetime | None]
    lease_expires_at: Mapped[datetime | None]


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
    __table_args__ = (
        Index("ix_approval_requests_run_id", "run_id"),
        CheckConstraint(
            "authorization_fingerprint IS NULL OR (length(authorization_fingerprint) = 64 "
            "AND authorization_fingerprint NOT GLOB '*[^0-9a-f]*')",
            name="ck_approval_authorization_fingerprint_hex",
        ),
    )

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
    expires_at: Mapped[datetime | None]
    resolved_at: Mapped[datetime | None]
    resolution_note: Mapped[str | None]
    resolver: Mapped[str | None]
    authorization_fingerprint: Mapped[str | None]
    consumed_at: Mapped[datetime | None]


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
    provenance_kind: Mapped[str | None]
    provenance_target: Mapped[str | None]
    provenance_remote_name: Mapped[str | None]
    provenance_binding_fingerprint: Mapped[str | None]


class OutboundDeliveryRow(Base):
    __tablename__ = "outbound_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "source_tool_invocation_id",
            name="uq_outbound_deliveries_source_tool_invocation_id",
        ),
        UniqueConstraint(
            "source_schedule_fire_id",
            name="uq_outbound_deliveries_source_schedule_fire_id",
        ),
        Index("ix_outbound_deliveries_due", "status", "available_at", "id"),
        CheckConstraint(
            "status IN ('queued', 'sending', 'delivered', 'failed', 'ambiguous', 'cancelled')",
            name="ck_outbound_deliveries_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_outbound_deliveries_attempt_count"),
        CheckConstraint("claim_generation >= 0", name="ck_outbound_deliveries_claim_generation"),
        CheckConstraint(
            "(source_kind = 'agent_request' AND source_tool_invocation_id IS NOT NULL "
            "AND source_schedule_fire_id IS NULL) OR "
            "(source_kind = 'scheduled_run_answer' AND source_tool_invocation_id IS NULL "
            "AND source_schedule_fire_id IS NOT NULL)",
            name="ck_outbound_deliveries_source_shape",
        ),
        CheckConstraint(
            "length(route_fingerprint) = 64 AND route_fingerprint NOT GLOB '*[^0-9a-f]*'",
            name="ck_outbound_deliveries_route_fingerprint_hex",
        ),
        CheckConstraint(
            "length(body_sha256) = 64 AND body_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_outbound_deliveries_body_sha256_hex",
        ),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    source_kind: Mapped[str]
    source_run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    source_tool_invocation_id: Mapped[str | None] = mapped_column(ForeignKey("tool_invocations.id"))
    source_schedule_fire_id: Mapped[str | None] = mapped_column(ForeignKey("schedule_fires.id"))
    route_id: Mapped[str]
    route_fingerprint: Mapped[str]
    subject: Mapped[str | None]
    body: Mapped[str]
    body_sha256: Mapped[str]
    status: Mapped[str]
    available_at: Mapped[datetime]
    attempt_count: Mapped[int]
    claim_owner: Mapped[str | None]
    claim_token: Mapped[str | None]
    claim_generation: Mapped[int]
    claim_expires_at: Mapped[datetime | None]
    provider_message_id: Mapped[str | None]
    failure_code: Mapped[str | None]
    failure_message: Mapped[str | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    delivered_at: Mapped[datetime | None]
    dispatch_started_at: Mapped[datetime | None]


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


class RunEventSequenceCounterRow(Base):
    __tablename__ = "run_event_sequence_counters"

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    next_value: Mapped[int]


class MemoryIndexSnapshotRow(Base):
    __tablename__ = "memory_index_snapshots"

    id: Mapped[str] = mapped_column(primary_key=True)
    vault_identity_hash: Mapped[str]
    source_snapshot_hash: Mapped[str]
    graph_checksum: Mapped[str | None]
    graphify_version: Mapped[str | None]
    status: Mapped[str]
    built_at: Mapped[datetime]
    file_count: Mapped[int]
    node_count: Mapped[int]
    edge_count: Mapped[int]
    failure_code: Mapped[str | None]


class MemoryRetrievalRecordRow(Base):
    __tablename__ = "memory_retrieval_records"
    __table_args__ = (Index("ix_memory_retrieval_records_run_id", "run_id"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    turn_number: Mapped[int]
    query_hash: Mapped[str]
    source_snapshot_id: Mapped[str | None]
    index_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("memory_index_snapshots.id"))
    created_at: Mapped[datetime]
    candidate_count: Mapped[int]
    selected_count: Mapped[int]


class MemoryRetrievalItemRow(Base):
    __tablename__ = "memory_retrieval_items"
    __table_args__ = (Index("ix_memory_retrieval_items_record_id", "record_id"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("memory_retrieval_records.id"))
    path: Mapped[str]
    heading: Mapped[str | None]
    start_line: Mapped[int]
    end_line: Mapped[int]
    content_hash: Mapped[str]
    rank: Mapped[int]
    methods: Mapped[object] = mapped_column(JSON)
    truncated: Mapped[bool]
