from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SkillRow(Base):
    __tablename__ = "skills"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled', 'archived')", name="ck_skills_status"),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(unique=True)
    display_name: Mapped[str]
    description: Mapped[str]
    status: Mapped[str]
    active_revision_id: Mapped[str | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class SkillRevisionRow(Base):
    __tablename__ = "skill_revisions"
    __table_args__ = (
        UniqueConstraint("skill_id", "version", name="uq_skill_revisions_skill_version"),
        CheckConstraint("version > 0", name="ck_skill_revisions_version"),
        CheckConstraint(
            "length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_skill_revisions_sha256",
        ),
        CheckConstraint(
            "source_kind IN ('operator', 'imported')", name="ck_skill_revisions_source_kind"
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"), index=True)
    version: Mapped[int]
    instructions: Mapped[str]
    content_sha256: Mapped[str]
    source_kind: Mapped[str]
    created_at: Mapped[datetime]


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
        UniqueConstraint("id", "schedule_id", "run_id", name="uq_schedule_fires_binding"),
        Index("ix_schedule_fires_schedule_id", "schedule_id"),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    schedule_id: Mapped[str] = mapped_column(ForeignKey("schedules.id"))
    scheduled_for: Mapped[datetime]
    fired_at: Mapped[datetime]
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))


class ScheduleDeliveryPolicyRow(Base):
    __tablename__ = "schedule_delivery_policies"
    __table_args__ = (
        CheckConstraint(
            "length(route_id) BETWEEN 1 AND 64", name="ck_schedule_delivery_policies_route_id"
        ),
        CheckConstraint("enabled IN (0, 1)", name="ck_schedule_delivery_policies_enabled"),
    )
    schedule_id: Mapped[str] = mapped_column(ForeignKey("schedules.id"), primary_key=True)
    route_id: Mapped[str]
    enabled: Mapped[bool]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class ScheduleFireDeliveryPlanRow(Base):
    __tablename__ = "schedule_fire_delivery_plans"
    __table_args__ = (
        UniqueConstraint("schedule_fire_id", name="uq_schedule_fire_delivery_plans_fire"),
        ForeignKeyConstraint(
            ["schedule_fire_id", "schedule_id", "execution_id"],
            ["schedule_fires.id", "schedule_fires.schedule_id", "schedule_fires.run_id"],
            name="fk_schedule_fire_delivery_plans_fire_binding",
        ),
        CheckConstraint(
            "status IN ('ready', 'suppressed')", name="ck_schedule_fire_delivery_plans_status"
        ),
        CheckConstraint(
            "content_source = 'final_agent_summary_v1'",
            name="ck_schedule_fire_delivery_plans_content",
        ),
        CheckConstraint(
            "route_fingerprint IS NULL OR (length(route_fingerprint) = 64 AND "
            "route_fingerprint NOT GLOB '*[^0-9a-f]*')",
            name="ck_schedule_fire_delivery_plans_fingerprint",
        ),
        CheckConstraint(
            "route_max_body_chars IS NULL OR route_max_body_chars > 0",
            name="ck_schedule_fire_delivery_plans_route_max_body_chars",
        ),
        CheckConstraint(
            "(status = 'ready' AND route_fingerprint IS NOT NULL AND reason_code IS NULL) OR "  # noqa: E501
            "(status = 'suppressed' AND route_fingerprint IS NULL AND route_max_body_chars IS NULL "
            "AND reason_code IN "
            "('schedule_delivery_route_missing', 'schedule_delivery_route_disabled'))",
            name="ck_schedule_fire_delivery_plans_shape",
        ),
        CheckConstraint(
            "length(route_id) BETWEEN 1 AND 64", name="ck_schedule_fire_delivery_plans_route_id"
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    schedule_fire_id: Mapped[str]
    schedule_id: Mapped[str]
    execution_id: Mapped[str]
    route_id: Mapped[str]
    route_fingerprint: Mapped[str | None]
    route_max_body_chars: Mapped[int | None]
    content_source: Mapped[str]
    status: Mapped[str]
    reason_code: Mapped[str | None]
    content_rejected_run_id: Mapped[str | None]
    created_at: Mapped[datetime]


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


class DeliveryAttemptRow(Base):
    __tablename__ = "delivery_attempts"
    __table_args__ = (
        UniqueConstraint(
            "delivery_id", "claim_generation", name="uq_delivery_attempts_delivery_generation"
        ),
        Index("ix_delivery_attempts_delivery_started", "delivery_id", "started_at"),
        CheckConstraint("claim_generation > 0", name="ck_delivery_attempts_claim_generation"),
        CheckConstraint(
            "outcome IN ('in_progress', 'delivered', 'failed', 'ambiguous')",
            name="ck_delivery_attempts_outcome",
        ),
        # The full lifecycle shape, enforced in the database so no write path
        # (ORM, raw SQL, or a future migration) can persist a combination the
        # domain's validate_delivery_attempt_shape would have rejected.
        CheckConstraint(
            "(outcome = 'in_progress' AND finished_at IS NULL AND failure_code IS NULL) OR "
            "(outcome = 'delivered' AND finished_at IS NOT NULL AND failure_code IS NULL) OR "
            "(outcome IN ('failed', 'ambiguous') AND finished_at IS NOT NULL "
            "AND failure_code IS NOT NULL)",
            name="ck_delivery_attempts_lifecycle",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_delivery_attempts_finished_after_started",
        ),
        # Stable lowercase code, bounded length, never free-form provider text.
        # GLOB is case-sensitive in SQLite (unlike LIKE), so the negated class
        # rejects anything outside [a-z0-9_] including uppercase and whitespace.
        CheckConstraint(
            "failure_code IS NULL OR (length(failure_code) BETWEEN 1 AND 128 "
            "AND failure_code NOT GLOB '*[^a-z0-9_]*')",
            name="ck_delivery_attempts_failure_code_shape",
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    delivery_id: Mapped[str] = mapped_column(ForeignKey("outbound_deliveries.id"), nullable=False)
    claim_generation: Mapped[int]
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    outcome: Mapped[str]
    failure_code: Mapped[str | None]


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
