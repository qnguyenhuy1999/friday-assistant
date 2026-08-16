from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SkillRow(Base):
    __tablename__ = "skills"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled', 'archived')", name="ck_skills_status"),
        # The durable ownership fence for the active pointer: (skills.id,
        # active_revision_id) must name a revision that belongs to this same
        # skill. NULL active_revision_id (no activation yet) skips the check.
        ForeignKeyConstraint(
            ["id", "active_revision_id"],
            ["skill_revisions.skill_id", "skill_revisions.id"],
            name="fk_skills_active_revision_ownership",
        ),
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
        # (skill_id, id) backs the skills.active_revision_id composite FK.
        UniqueConstraint("skill_id", "id", name="uq_skill_revisions_skill_id"),
        CheckConstraint("version > 0", name="ck_skill_revisions_version"),
        CheckConstraint(
            "length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_skill_revisions_sha256",
        ),
        CheckConstraint(
            "source_kind IN ('operator', 'imported', 'generated')",
            name="ck_skill_revisions_source_kind",
        ),
        CheckConstraint(
            "(source_kind = 'generated' AND promotion_request_id IS NOT NULL) OR "
            "(source_kind IN ('operator', 'imported') AND promotion_request_id IS NULL)",
            name="ck_skill_revisions_promotion_provenance",
        ),
        UniqueConstraint("promotion_request_id", name="uq_skill_revisions_promotion_request"),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"), index=True)
    version: Mapped[int]
    instructions: Mapped[str]
    content_sha256: Mapped[str]
    source_kind: Mapped[str]
    created_at: Mapped[datetime]
    promotion_request_id: Mapped[str | None] = mapped_column(
        ForeignKey("skill_promotion_requests.id")
    )


class TaskSkillBindingRow(Base):
    __tablename__ = "task_skill_bindings"
    __table_args__ = (
        UniqueConstraint("task_id", "skill_id", name="uq_task_skill_bindings_skill"),
        UniqueConstraint("task_id", "position", name="uq_task_skill_bindings_position"),
        CheckConstraint("position BETWEEN 1 AND 16", name="ck_task_skill_bindings_position"),
    )
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"), primary_key=True)
    position: Mapped[int]
    created_at: Mapped[datetime]


class RunSkillResolutionRow(Base):
    __tablename__ = "run_skill_resolutions"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_run_skill_resolutions_run_id"),
        UniqueConstraint("run_id", "id", name="uq_run_skill_resolutions_run_id_id"),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    resolved_at: Mapped[datetime]


class RunSkillBindingRow(Base):
    __tablename__ = "run_skill_bindings"
    __table_args__ = (
        UniqueConstraint("run_id", "skill_id", name="uq_run_skill_bindings_skill"),
        UniqueConstraint(
            "run_id", "skill_id", "revision_id", name="uq_run_skill_bindings_frozen_revision"
        ),
        UniqueConstraint("run_id", "position", name="uq_run_skill_bindings_position"),
        ForeignKeyConstraint(
            ["skill_id", "revision_id"],
            ["skill_revisions.skill_id", "skill_revisions.id"],
            name="fk_run_skill_bindings_revision_ownership",
        ),
        CheckConstraint("position BETWEEN 1 AND 16", name="ck_run_skill_bindings_position"),
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    skill_id: Mapped[str] = mapped_column(primary_key=True)
    revision_id: Mapped[str]
    position: Mapped[int]


class SkillUsageRecordRow(Base):
    __tablename__ = "skill_usage_records"
    __table_args__ = (
        UniqueConstraint("run_id", "skill_id", name="uq_skill_usage_records_run_skill"),
        ForeignKeyConstraint(
            ["run_id", "skill_id", "revision_id"],
            [
                "run_skill_bindings.run_id",
                "run_skill_bindings.skill_id",
                "run_skill_bindings.revision_id",
            ],
            name="fk_skill_usage_records_frozen_binding",
        ),
        ForeignKeyConstraint(
            ["run_id", "resolution_id"],
            ["run_skill_resolutions.run_id", "run_skill_resolutions.id"],
            name="fk_skill_usage_records_resolution_ownership",
        ),
        ForeignKeyConstraint(
            ["run_id", "task_id"],
            ["runs.id", "runs.task_id"],
            name="fk_skill_usage_records_task_ownership",
        ),
        ForeignKeyConstraint(
            ["run_id", "execution_id"],
            ["runs.id", "runs.execution_id"],
            name="fk_skill_usage_records_execution_ownership",
        ),
        CheckConstraint(
            "outcome IN ('succeeded', 'failed', 'cancelled', 'resolution_failed')",
            name="ck_skill_usage_records_outcome",
        ),
        CheckConstraint("attempt_number > 0", name="ck_skill_usage_records_attempt"),
        CheckConstraint("tool_call_count >= 0", name="ck_skill_usage_records_tool_count"),
        CheckConstraint("approval_count >= 0", name="ck_skill_usage_records_approval_count"),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="ck_skill_usage_records_duration"
        ),
        CheckConstraint("position BETWEEN 1 AND 16", name="ck_skill_usage_records_position"),
        CheckConstraint(
            "(outcome = 'failed' AND failure_code IS NOT NULL AND "
            "length(failure_code) BETWEEN 1 AND 128 AND failure_code NOT GLOB '*[^a-z0-9_]*') OR "
            "(outcome <> 'failed' AND failure_code IS NULL)",
            name="ck_skill_usage_records_failure_code_shape",
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"), nullable=False, index=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("skill_revisions.id"), nullable=False)
    position: Mapped[int]
    resolution_id: Mapped[str] = mapped_column(
        ForeignKey("run_skill_resolutions.id"), nullable=False
    )
    execution_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    attempt_number: Mapped[int]
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime]
    outcome: Mapped[str]
    failure_code: Mapped[str | None]
    tool_call_count: Mapped[int]
    approval_count: Mapped[int]
    duration_ms: Mapped[int | None]
    created_at: Mapped[datetime]


class SkillRunFeedbackRow(Base):
    __tablename__ = "skill_run_feedback"
    __table_args__ = (
        CheckConstraint(
            "rating IN ('helpful', 'neutral', 'harmful')", name="ck_skill_feedback_rating"
        ),
        CheckConstraint("length(created_by) BETWEEN 1 AND 128", name="ck_skill_feedback_creator"),
        ForeignKeyConstraint(
            ["run_id", "skill_id", "revision_id"],
            [
                "run_skill_bindings.run_id",
                "run_skill_bindings.skill_id",
                "run_skill_bindings.revision_id",
            ],
            name="fk_skill_feedback_frozen_binding",
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"), nullable=False, index=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("skill_revisions.id"), nullable=False)
    rating: Mapped[str]
    note: Mapped[str]
    created_by: Mapped[str]
    created_at: Mapped[datetime]


class SkillEvaluationSuiteRow(Base):
    __tablename__ = "skill_evaluation_suites"
    __table_args__ = (
        UniqueConstraint("skill_id", "id", name="uq_skill_evaluation_suites_skill_id"),
        UniqueConstraint("skill_id", "name", name="uq_skill_evaluation_suite_name"),
        CheckConstraint(
            "status IN ('active', 'disabled')", name="ck_skill_evaluation_suites_status"
        ),
        CheckConstraint("length(name) BETWEEN 1 AND 256", name="ck_skill_evaluation_suites_name"),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"))
    name: Mapped[str]
    description: Mapped[str]
    status: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class SkillEvaluationCaseRow(Base):
    __tablename__ = "skill_evaluation_cases"
    __table_args__ = (
        UniqueConstraint("suite_id", "position", name="uq_skill_evaluation_case_position"),
        CheckConstraint("position > 0", name="ck_skill_evaluation_case_position"),
        CheckConstraint("length(input) BETWEEN 1 AND 32000", name="ck_skill_evaluation_case_input"),
        CheckConstraint(
            "grading_kind IN ('exact_match', 'contains_all', 'contains_none', "
            "'json_schema', 'required_keys', 'tool_proposal_shape')",
            name="ck_skill_evaluation_case_grading_kind",
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    suite_id: Mapped[str] = mapped_column(ForeignKey("skill_evaluation_suites.id"))
    position: Mapped[int]
    input: Mapped[str]
    expected_properties: Mapped[object] = mapped_column(JSON)
    grading_kind: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class SkillEvaluationRunRow(Base):
    __tablename__ = "skill_evaluation_runs"
    __table_args__ = (
        UniqueConstraint("skill_id", "id", name="uq_skill_evaluation_runs_skill_id"),
        ForeignKeyConstraint(
            ["skill_id", "suite_id"],
            ["skill_evaluation_suites.skill_id", "skill_evaluation_suites.id"],
            name="fk_skill_evaluation_runs_suite_ownership",
        ),
        ForeignKeyConstraint(
            ["skill_id", "revision_id"],
            ["skill_revisions.skill_id", "skill_revisions.id"],
            name="fk_skill_evaluation_runs_revision_ownership",
        ),
        ForeignKeyConstraint(
            ["skill_id", "proposal_id"],
            ["skill_improvement_proposals.skill_id", "skill_improvement_proposals.id"],
            name="fk_skill_evaluation_runs_proposal_ownership",
        ),
        CheckConstraint(
            "(revision_id IS NOT NULL AND proposal_id IS NULL) OR "
            "(revision_id IS NULL AND proposal_id IS NOT NULL)",
            name="ck_skill_evaluation_runs_target_xor",
        ),
        CheckConstraint(
            "status IN ('succeeded', 'failed')", name="ck_skill_evaluation_runs_status"
        ),
        CheckConstraint(
            "length(runtime_fingerprint) = 64 AND runtime_fingerprint NOT GLOB '*[^0-9a-f]*'",
            name="ck_skill_evaluation_runs_runtime_fingerprint",
        ),
        CheckConstraint(
            "length(target_content_sha256) = 64 AND target_content_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_skill_evaluation_runs_target_sha256",
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    suite_id: Mapped[str] = mapped_column(ForeignKey("skill_evaluation_suites.id"))
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"))
    revision_id: Mapped[str | None] = mapped_column(ForeignKey("skill_revisions.id"))
    proposal_id: Mapped[str | None] = mapped_column(
        ForeignKey("skill_improvement_proposals.id"), index=True
    )
    status: Mapped[str]
    evaluator_version: Mapped[str]
    started_at: Mapped[datetime]
    completed_at: Mapped[datetime]
    aggregate_result: Mapped[object] = mapped_column(JSON)
    suite_snapshot: Mapped[object] = mapped_column(JSON)
    runtime_fingerprint: Mapped[str]
    target_content_sha256: Mapped[str]
    runtime_metadata: Mapped[object] = mapped_column(JSON)
    call_usage: Mapped[object | None] = mapped_column(JSON, nullable=True)
    case_results: Mapped[list[SkillEvaluationCaseResultRow]] = relationship(back_populates="run")


class SkillEvaluationCaseResultRow(Base):
    __tablename__ = "skill_evaluation_case_results"
    __table_args__ = (
        UniqueConstraint("evaluation_run_id", "case_id", name="uq_skill_evaluation_case_result"),
        CheckConstraint("score BETWEEN 0 AND 1", name="ck_skill_evaluation_case_score"),
        CheckConstraint(
            "status IN ('succeeded', 'failed')", name="ck_skill_evaluation_case_status"
        ),
        CheckConstraint(
            "length(output_sha256) = 64 AND output_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_skill_evaluation_case_output_sha256",
        ),
    )
    evaluation_run_id: Mapped[str] = mapped_column(
        ForeignKey("skill_evaluation_runs.id"), primary_key=True
    )
    case_id: Mapped[str] = mapped_column(ForeignKey("skill_evaluation_cases.id"), primary_key=True)
    status: Mapped[str]
    score: Mapped[float]
    reason_code: Mapped[str | None]
    bounded_details: Mapped[str]
    output_sha256: Mapped[str]
    run: Mapped[SkillEvaluationRunRow] = relationship(back_populates="case_results")


class SkillImprovementProposalRow(Base):
    __tablename__ = "skill_improvement_proposals"
    __table_args__ = (
        UniqueConstraint(
            "skill_id",
            "base_revision_id",
            "evidence_snapshot_hash",
            "generator_version",
            name="uq_skill_improvement_proposal_fingerprint",
        ),
        UniqueConstraint("skill_id", "id", name="uq_skill_improvement_proposals_skill_id"),
        ForeignKeyConstraint(
            ["skill_id", "base_revision_id"],
            ["skill_revisions.skill_id", "skill_revisions.id"],
            name="fk_skill_improvement_proposals_base_revision_ownership",
        ),
        ForeignKeyConstraint(
            ["skill_id", "evidence_snapshot_id"],
            ["skill_evidence_snapshots.skill_id", "skill_evidence_snapshots.id"],
            name="fk_skill_improvement_proposals_evidence_snapshot_ownership",
        ),
        CheckConstraint(
            "status IN ('draft', 'ready_for_evaluation', 'evaluating', 'ready_for_review', "
            "'approved', 'rejected', 'superseded', 'cancelled', 'expired', 'promoted')",
            name="ck_skill_improvement_proposals_status",
        ),
        CheckConstraint(
            "length(evidence_snapshot_hash) = 64 AND evidence_snapshot_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_skill_improvement_proposals_evidence_hash",
        ),
        CheckConstraint(
            "length(proposed_content_sha256) = 64 AND "
            "proposed_content_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_skill_improvement_proposals_content_hash",
        ),
        CheckConstraint(
            "length(candidate_prompt_sha256) = 64 AND "
            "candidate_prompt_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_skill_improvement_proposals_candidate_prompt_hash",
        ),
        Index(
            "uq_skill_improvement_proposals_one_open",
            "skill_id",
            unique=True,
            sqlite_where=text(
                "status IN ('draft', 'ready_for_evaluation', 'evaluating', "
                "'ready_for_review', 'approved')"
            ),
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"), index=True)
    base_revision_id: Mapped[str] = mapped_column(ForeignKey("skill_revisions.id"))
    status: Mapped[str]
    trigger_kind: Mapped[str]
    evidence_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("skill_evidence_snapshots.id"), nullable=False
    )
    evidence_snapshot_hash: Mapped[str]
    proposed_instructions: Mapped[str]
    proposed_content_sha256: Mapped[str]
    rationale: Mapped[str]
    generator_version: Mapped[str]
    candidate_prompt_version: Mapped[str]
    candidate_prompt_sha256: Mapped[str]
    created_at: Mapped[datetime]


class SkillImprovementWorkRow(Base):
    __tablename__ = "skill_improvement_work_items"
    __table_args__ = (
        Index(
            "uq_skill_improvement_work_active_skill",
            "skill_id",
            unique=True,
            sqlite_where=text(
                "state IN ('evidence_selection', 'candidate_generation', "
                "'baseline_evaluation', 'candidate_evaluation', 'comparison', 'failed')"
            ),
        ),
        CheckConstraint(
            "state IN ('evidence_selection', 'candidate_generation', 'baseline_evaluation', "
            "'candidate_evaluation', 'comparison', 'ready_for_review', 'failed', 'complete')",
            name="ck_skill_improvement_work_state",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_skill_improvement_work_attempt_count"),
        CheckConstraint("claim_generation >= 0", name="ck_skill_improvement_work_claim_generation"),
        CheckConstraint(
            "failure_code IS NULL OR (length(failure_code) BETWEEN 1 AND 128 "
            "AND failure_code NOT GLOB '*[^a-z0-9_]*')",
            name="ck_skill_improvement_work_failure_code",
        ),
        CheckConstraint(
            "(claimed_by IS NULL AND claim_token IS NULL AND lease_expires_at IS NULL) OR "
            "(claimed_by IS NOT NULL AND claim_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_skill_improvement_work_claim_shape",
        ),
        CheckConstraint(
            "failure_detail IS NULL OR length(failure_detail) BETWEEN 1 AND 512",
            name="ck_skill_improvement_work_failure_detail",
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"), nullable=False)
    state: Mapped[str]
    proposal_id: Mapped[str | None] = mapped_column(
        ForeignKey("skill_improvement_proposals.id"), nullable=True
    )
    attempt_count: Mapped[int]
    next_attempt_at: Mapped[datetime]
    claimed_by: Mapped[str | None]
    claim_token: Mapped[str | None]
    claim_generation: Mapped[int]
    lease_expires_at: Mapped[datetime | None]
    failure_code: Mapped[str | None]
    failure_detail: Mapped[str | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class SkillCandidateEvaluationRow(Base):
    __tablename__ = "skill_candidate_evaluations"
    __table_args__ = (
        UniqueConstraint("proposal_id", "id", name="uq_skill_candidate_evaluations_proposal_id"),
        CheckConstraint(
            "result IN ('better', 'worse', 'equivalent', 'mixed', 'inconclusive')",
            name="ck_skill_candidate_evaluations_result",
        ),
        CheckConstraint(
            "recommendation IN ('eligible', 'not_eligible', 'requires_manual_override')",
            name="ck_skill_candidate_evaluations_recommendation",
        ),
        CheckConstraint(
            "regression_count >= 0 AND improvement_count >= 0 AND inconclusive_count >= 0",
            name="ck_skill_candidate_evaluations_counts",
        ),
        CheckConstraint(
            "length(report_sha256) = 64 AND report_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_skill_candidate_evaluations_report_sha256",
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("skill_improvement_proposals.id"), unique=True
    )
    baseline_evaluation_run_id: Mapped[str] = mapped_column(ForeignKey("skill_evaluation_runs.id"))
    candidate_evaluation_run_id: Mapped[str] = mapped_column(
        ForeignKey("skill_evaluation_runs.id"), unique=True
    )
    comparison_policy_version: Mapped[str]
    result: Mapped[str]
    recommendation: Mapped[str]
    score_delta: Mapped[float]
    regression_count: Mapped[int]
    improvement_count: Mapped[int]
    inconclusive_count: Mapped[int]
    report_sha256: Mapped[str]
    created_at: Mapped[datetime]
    comparison_report: Mapped[object] = mapped_column(JSON, nullable=False)


class SkillPromotionRequestRow(Base):
    __tablename__ = "skill_promotion_requests"
    __table_args__ = (
        CheckConstraint("target_version > 0", name="ck_skill_promotion_target_version"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'stale', 'cancelled', 'promoted')",
            name="ck_skill_promotion_status",
        ),
        CheckConstraint(
            "length(candidate_sha256) = 64 AND candidate_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_skill_promotion_candidate_sha256",
        ),
        CheckConstraint(
            "length(comparison_report_sha256) = 64 AND "
            "comparison_report_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_skill_promotion_report_sha256",
        ),
        CheckConstraint(
            "length(authorization_fingerprint) = 64 AND "
            "authorization_fingerprint NOT GLOB '*[^0-9a-f]*'",
            name="ck_skill_promotion_authorization_fingerprint",
        ),
        UniqueConstraint("target_revision_id", name="uq_skill_promotion_target_revision"),
        ForeignKeyConstraint(
            ["skill_id", "proposal_id"],
            ["skill_improvement_proposals.skill_id", "skill_improvement_proposals.id"],
            name="fk_skill_promotion_proposal_ownership",
        ),
        ForeignKeyConstraint(
            ["proposal_id", "candidate_evaluation_id"],
            ["skill_candidate_evaluations.proposal_id", "skill_candidate_evaluations.id"],
            name="fk_skill_promotion_candidate_evaluation_ownership",
        ),
        ForeignKeyConstraint(
            ["skill_id", "base_revision_id"],
            ["skill_revisions.skill_id", "skill_revisions.id"],
            name="fk_skill_promotion_base_revision_ownership",
        ),
        ForeignKeyConstraint(
            ["skill_id", "expected_active_revision_id"],
            ["skill_revisions.skill_id", "skill_revisions.id"],
            name="fk_skill_promotion_expected_active_ownership",
        ),
        UniqueConstraint("approval_request_id", name="uq_skill_promotion_approval_request"),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("skill_improvement_proposals.id"), unique=True
    )
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"))
    base_revision_id: Mapped[str] = mapped_column(ForeignKey("skill_revisions.id"))
    expected_active_revision_id: Mapped[str] = mapped_column(ForeignKey("skill_revisions.id"))
    candidate_sha256: Mapped[str]
    candidate_evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("skill_candidate_evaluations.id")
    )
    comparison_report_sha256: Mapped[str]
    target_revision_id: Mapped[str]
    target_version: Mapped[int]
    authorization_fingerprint: Mapped[str]
    status: Mapped[str]
    created_at: Mapped[datetime]
    resolved_at: Mapped[datetime | None]
    resolver: Mapped[str | None]
    promoted_revision_id: Mapped[str | None] = mapped_column(ForeignKey("skill_revisions.id"))
    approval_request_id: Mapped[str] = mapped_column(
        ForeignKey("approval_requests.id"), nullable=False
    )


class SkillRollbackRequestRow(Base):
    __tablename__ = "skill_rollback_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'stale', 'cancelled', 'completed')",
            name="ck_skill_rollback_status",
        ),
        CheckConstraint(
            "length(authorization_fingerprint) = 64 AND "
            "authorization_fingerprint NOT GLOB '*[^0-9a-f]*'",
            name="ck_skill_rollback_authorization_fingerprint",
        ),
        CheckConstraint(
            "target_revision_id <> expected_current_revision_id",
            name="ck_skill_rollback_target_not_current",
        ),
        ForeignKeyConstraint(
            ["skill_id", "expected_current_revision_id"],
            ["skill_revisions.skill_id", "skill_revisions.id"],
            name="fk_skill_rollback_current_revision_ownership",
        ),
        ForeignKeyConstraint(
            ["skill_id", "target_revision_id"],
            ["skill_revisions.skill_id", "skill_revisions.id"],
            name="fk_skill_rollback_target_revision_ownership",
        ),
        UniqueConstraint("approval_request_id", name="uq_skill_rollback_approval_request"),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"))
    expected_current_revision_id: Mapped[str] = mapped_column(ForeignKey("skill_revisions.id"))
    target_revision_id: Mapped[str] = mapped_column(ForeignKey("skill_revisions.id"))
    reason: Mapped[str]
    authorization_fingerprint: Mapped[str]
    status: Mapped[str]
    created_at: Mapped[datetime]
    resolved_at: Mapped[datetime | None]
    resolver: Mapped[str | None]
    approval_request_id: Mapped[str] = mapped_column(
        ForeignKey("approval_requests.id"), nullable=False
    )


class SkillImprovementPolicyRow(Base):
    __tablename__ = "skill_improvement_policies"
    __table_args__ = (
        CheckConstraint(
            "minimum_usage_records >= 0 AND minimum_failures >= 0 AND "
            "minimum_harmful_feedback >= 0",
            name="ck_skill_improvement_policy_thresholds",
        ),
        CheckConstraint("cooldown_seconds >= 0", name="ck_skill_improvement_policy_cooldown"),
        CheckConstraint(
            "max_open_proposals = 1 AND evidence_window_size BETWEEN 1 AND 200",
            name="ck_skill_improvement_policy_bounds",
        ),
    )
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"), primary_key=True)
    enabled: Mapped[bool]
    minimum_usage_records: Mapped[int]
    minimum_failures: Mapped[int]
    minimum_harmful_feedback: Mapped[int]
    evaluation_suite_id: Mapped[str] = mapped_column(ForeignKey("skill_evaluation_suites.id"))
    cooldown_seconds: Mapped[int]
    max_open_proposals: Mapped[int]
    evidence_window_size: Mapped[int]
    generator_version: Mapped[str]
    comparison_policy_version: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    last_triggered_at: Mapped[datetime | None]


class SkillEvidenceSnapshotRow(Base):
    __tablename__ = "skill_evidence_snapshots"
    __table_args__ = (
        UniqueConstraint("skill_id", "id", name="uq_skill_evidence_snapshots_skill_id"),
        UniqueConstraint(
            "skill_id", "base_revision_id", "id", name="uq_skill_evidence_snapshot_ownership"
        ),
        ForeignKeyConstraint(
            ["skill_id", "base_revision_id"],
            ["skill_revisions.skill_id", "skill_revisions.id"],
            name="fk_skill_evidence_snapshots_base_revision_ownership",
        ),
        CheckConstraint(
            "length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_skill_evidence_snapshot_sha256",
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"))
    base_revision_id: Mapped[str] = mapped_column(ForeignKey("skill_revisions.id"))
    evidence: Mapped[object] = mapped_column(JSON)
    content_sha256: Mapped[str]
    created_at: Mapped[datetime]


class AgentRow(Base):
    __tablename__ = "agents"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled', 'archived')", name="ck_agents_status"),
        # The durable ownership fence for the active pointer: (agents.id,
        # active_revision_id) must name a revision that belongs to this same
        # agent. NULL active_revision_id (no activation yet) skips the check.
        ForeignKeyConstraint(
            ["id", "active_revision_id"],
            ["agent_revisions.agent_id", "agent_revisions.id"],
            name="fk_agents_active_revision_ownership",
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(unique=True)
    display_name: Mapped[str]
    description: Mapped[str]
    status: Mapped[str]
    active_revision_id: Mapped[str | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class AgentRevisionRow(Base):
    __tablename__ = "agent_revisions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_agent_revisions_agent_version"),
        # (agent_id, id) backs the agents.active_revision_id composite FK.
        UniqueConstraint("agent_id", "id", name="uq_agent_revisions_agent_id"),
        CheckConstraint("version > 0", name="ck_agent_revisions_version"),
        CheckConstraint(
            "length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_agent_revisions_sha256",
        ),
        CheckConstraint(
            "source_kind IN ('operator', 'imported')", name="ck_agent_revisions_source_kind"
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    version: Mapped[int]
    instructions: Mapped[str]
    runtime_kind: Mapped[str]
    runtime_config: Mapped[object] = mapped_column(JSON)
    content_sha256: Mapped[str]
    source_kind: Mapped[str]
    created_at: Mapped[datetime]


class WorkflowRow(Base):
    __tablename__ = "workflows"
    __table_args__ = (
        CheckConstraint("status IN ('active','disabled','archived')", name="ck_workflows_status"),
        CheckConstraint("length(key) BETWEEN 1 AND 128", name="ck_workflows_key"),
        ForeignKeyConstraint(
            ["id", "active_revision_id"],
            ["workflow_revisions.workflow_id", "workflow_revisions.id"],
            name="fk_workflows_active_revision_ownership",
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(unique=True)
    display_name: Mapped[str]
    description: Mapped[str]
    status: Mapped[str]
    active_revision_id: Mapped[str | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class WorkflowRevisionRow(Base):
    __tablename__ = "workflow_revisions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "version", name="uq_workflow_revisions_workflow_version"),
        UniqueConstraint("workflow_id", "id", name="uq_workflow_revisions_workflow_id"),
        CheckConstraint("version > 0", name="ck_workflow_revisions_version"),
        CheckConstraint(
            "source_kind IN ('operator','imported')", name="ck_workflow_revisions_source_kind"
        ),
        CheckConstraint(
            "length(content_sha256)=64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_workflow_revisions_sha256",
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    version: Mapped[int]
    content_sha256: Mapped[str]
    source_kind: Mapped[str]
    created_at: Mapped[datetime]


class WorkflowNodeRow(Base):
    __tablename__ = "workflow_nodes"
    __table_args__ = (
        UniqueConstraint("revision_id", "id", name="uq_workflow_nodes_revision_id"),
        UniqueConstraint("revision_id", "node_key", name="uq_workflow_nodes_revision_key"),
        CheckConstraint("length(node_key) BETWEEN 1 AND 128", name="ck_workflow_nodes_key"),
        CheckConstraint("length(objective) BETWEEN 1 AND 4000", name="ck_workflow_nodes_objective"),
        CheckConstraint(
            "length(expected_output_contract) BETWEEN 1 AND 4000", name="ck_workflow_nodes_output"
        ),
        ForeignKeyConstraint(
            ["revision_id"], ["workflow_revisions.id"], name="fk_workflow_nodes_revision"
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    revision_id: Mapped[str] = mapped_column(index=True)
    node_key: Mapped[str]
    target_agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    objective: Mapped[str]
    input_payload: Mapped[object] = mapped_column(JSON)
    expected_output_contract: Mapped[str]
    created_at: Mapped[datetime]


class WorkflowEdgeRow(Base):
    __tablename__ = "workflow_edges"
    __table_args__ = (
        UniqueConstraint("revision_id", "id", name="uq_workflow_edges_revision_id"),
        UniqueConstraint(
            "revision_id", "from_node_id", "to_node_id", name="uq_workflow_edges_pair"
        ),
        CheckConstraint("from_node_id <> to_node_id", name="ck_workflow_edges_not_self"),
        ForeignKeyConstraint(
            ["revision_id", "from_node_id"],
            ["workflow_nodes.revision_id", "workflow_nodes.id"],
            name="fk_workflow_edges_from_ownership",
        ),
        ForeignKeyConstraint(
            ["revision_id", "to_node_id"],
            ["workflow_nodes.revision_id", "workflow_nodes.id"],
            name="fk_workflow_edges_to_ownership",
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    revision_id: Mapped[str] = mapped_column(index=True)
    from_node_id: Mapped[str]
    to_node_id: Mapped[str]
    created_at: Mapped[datetime]


class TaskAgentBindingRow(Base):
    __tablename__ = "task_agent_bindings"
    __table_args__ = (
        # task_id is the primary key, but this explicit candidate key is the
        # target of DelegationRequest's composite ownership FK.
        UniqueConstraint("task_id", "agent_id", name="uq_task_agent_bindings_task_agent"),
    )

    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    created_at: Mapped[datetime]


class RunAgentResolutionRow(Base):
    __tablename__ = "run_agent_resolutions"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_run_agent_resolutions_run_id"),
        ForeignKeyConstraint(
            ["agent_id", "revision_id"],
            ["agent_revisions.agent_id", "agent_revisions.id"],
            name="fk_run_agent_resolutions_revision_ownership",
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    revision_id: Mapped[str] = mapped_column(ForeignKey("agent_revisions.id"))
    resolved_at: Mapped[datetime]


class DelegationRequestRow(Base):
    __tablename__ = "delegation_requests"
    __table_args__ = (
        Index("ix_delegation_requests_parent_run_id", "parent_run_id"),
        CheckConstraint(
            "status IN ('requested', 'dispatched', 'succeeded', 'failed', 'cancelled')",
            name="ck_delegation_requests_status",
        ),
        CheckConstraint(
            "length(authorization_fingerprint) = 64 AND "
            "authorization_fingerprint NOT GLOB '*[^0-9a-f]*'",
            name="ck_delegation_requests_fingerprint",
        ),
        CheckConstraint(
            "(status = 'failed' AND failure_code IS NOT NULL) OR "
            "(status != 'failed' AND failure_code IS NULL)",
            name="ck_delegation_requests_failure_shape",
        ),
        CheckConstraint(
            "length(objective) BETWEEN 1 AND 4000",
            name="ck_delegation_requests_objective_length",
        ),
        CheckConstraint(
            "length(expected_output_contract) BETWEEN 1 AND 4000",
            name="ck_delegation_requests_output_contract_length",
        ),
        UniqueConstraint("authorization_fingerprint", name="uq_delegation_requests_fingerprint"),
        UniqueConstraint("id", "parent_run_id", name="uq_delegation_requests_id_parent"),
        # NULL parent_run_step_id (no step scoping) skips the composite FK
        # check; a non-NULL value must name a step belonging to this exact
        # parent run, not merely any existing run_steps.id.
        ForeignKeyConstraint(
            ["parent_run_id", "parent_run_step_id"],
            ["run_steps.run_id", "run_steps.id"],
            name="fk_delegation_requests_step_ownership",
        ),
        CheckConstraint(
            "child_run_id IS NULL OR child_task_id IS NOT NULL",
            name="ck_delegation_requests_child_run_task_shape",
        ),
        CheckConstraint(
            "(status = 'requested' AND child_task_id IS NULL AND child_run_id IS NULL "
            "AND started_at IS NULL AND completed_at IS NULL AND failure_code IS NULL) OR "
            "(status = 'dispatched' AND child_task_id IS NOT NULL AND child_run_id IS NOT NULL "
            "AND started_at IS NOT NULL AND completed_at IS NULL AND failure_code IS NULL) OR "
            "(status = 'succeeded' AND child_task_id IS NOT NULL AND child_run_id IS NOT NULL "
            "AND started_at IS NOT NULL AND completed_at IS NOT NULL AND failure_code IS NULL) OR "
            "(status = 'failed' AND child_task_id IS NOT NULL AND child_run_id IS NOT NULL "
            "AND started_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND failure_code IS NOT NULL) OR "
            "(status = 'cancelled' AND failure_code IS NULL AND "
            "((child_task_id IS NULL AND child_run_id IS NULL AND started_at IS NULL) OR "
            "(child_task_id IS NOT NULL AND child_run_id IS NOT NULL AND started_at IS NOT NULL)) "
            "AND completed_at IS NOT NULL)",
            name="ck_delegation_requests_state_shape",
        ),
        UniqueConstraint("child_task_id", name="uq_delegation_requests_child_task_id"),
        UniqueConstraint("child_run_id", name="uq_delegation_requests_child_run_id"),
        ForeignKeyConstraint(
            ["child_run_id", "child_task_id"],
            ["runs.id", "runs.task_id"],
            name="fk_delegation_requests_child_run_task_ownership",
        ),
        ForeignKeyConstraint(
            ["child_task_id", "target_agent_id"],
            ["task_agent_bindings.task_id", "task_agent_bindings.agent_id"],
            name="fk_delegation_requests_child_agent_ownership",
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    parent_run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    parent_run_step_id: Mapped[str | None]
    target_agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    objective: Mapped[str]
    input_payload: Mapped[object] = mapped_column(JSON)
    expected_output_contract: Mapped[str]
    authorization_fingerprint: Mapped[str]
    status: Mapped[str]
    child_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    child_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    created_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    failure_code: Mapped[str | None]


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
    __table_args__ = (
        Index("ix_runs_task_id", "task_id"),
        UniqueConstraint("id", "task_id", name="uq_runs_id_task"),
        UniqueConstraint("id", "execution_id", name="uq_runs_id_execution"),
        CheckConstraint(
            "status IN ('queued', 'running', 'waiting_for_approval', "
            "'waiting_for_delegation', 'waiting_for_workflow', 'succeeded', 'failed', "
            "'cancelled')",
            name="ck_runs_status",
        ),
        CheckConstraint(
            "((status = 'waiting_for_approval' AND approval_request_id IS NOT NULL "
            "AND delegation_request_id IS NULL AND workflow_execution_id IS NULL) OR "
            "(status = 'waiting_for_delegation' AND delegation_request_id IS NOT NULL "
            "AND approval_request_id IS NULL AND workflow_execution_id IS NULL) OR "
            "(status = 'waiting_for_workflow' AND workflow_execution_id IS NOT NULL "
            "AND approval_request_id IS NULL AND delegation_request_id IS NULL) OR "
            "(status NOT IN ('waiting_for_approval', 'waiting_for_delegation', "
            "'waiting_for_workflow') AND approval_request_id IS NULL "
            "AND delegation_request_id IS NULL AND "
            "(workflow_execution_id IS NULL OR status IN ('succeeded', 'failed', 'cancelled'))))",
            name="ck_runs_wait_marker_shape",
        ),
        ForeignKeyConstraint(
            ["delegation_request_id", "id"],
            ["delegation_requests.id", "delegation_requests.parent_run_id"],
            name="fk_runs_delegation_parent_ownership",
        ),
        ForeignKeyConstraint(
            ["workflow_execution_id", "id"],
            ["workflow_executions.id", "workflow_executions.root_run_id"],
            name="fk_runs_workflow_execution_root_ownership",
        ),
    )

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
    delegation_request_id: Mapped[str | None] = mapped_column(index=True)
    workflow_execution_id: Mapped[str | None] = mapped_column(index=True)


class WorkflowExecutionRow(Base):
    __tablename__ = "workflow_executions"
    __table_args__ = (
        Index("ix_workflow_executions_root_run_id", "root_run_id"),
        UniqueConstraint("root_run_id", name="uq_workflow_executions_root_run_id"),
        # This candidate key is the target of RunRow's composite ownership FK.
        UniqueConstraint("id", "root_run_id", name="uq_workflow_executions_id_root_run"),
        # This candidate key is the target of WorkflowNodeExecutionRow's
        # composite frozen-revision ownership FK.
        UniqueConstraint("id", "workflow_revision_id", name="uq_workflow_executions_id_revision"),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled')",
            name="ck_workflow_executions_status",
        ),
        CheckConstraint(
            "length(workflow_content_sha256) = 64 AND "
            "workflow_content_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_workflow_executions_sha256",
        ),
        CheckConstraint(
            "(status = 'running' AND completed_at IS NULL AND failure_code IS NULL "
            "AND failure_message IS NULL) OR "
            "(status = 'succeeded' AND completed_at IS NOT NULL AND failure_code IS NULL "
            "AND failure_message IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL AND failure_code IS NOT NULL "
            "AND failure_message IS NOT NULL) OR "
            "(status = 'cancelled' AND completed_at IS NOT NULL AND failure_code IS NULL)",
            name="ck_workflow_executions_state_shape",
        ),
        ForeignKeyConstraint(
            ["workflow_id", "workflow_revision_id"],
            ["workflow_revisions.workflow_id", "workflow_revisions.id"],
            name="fk_workflow_executions_revision_ownership",
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    root_run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"))
    workflow_revision_id: Mapped[str]
    workflow_content_sha256: Mapped[str]
    status: Mapped[str]
    started_at: Mapped[datetime]
    completed_at: Mapped[datetime | None]
    failure_code: Mapped[str | None]
    failure_message: Mapped[str | None]


class WorkflowNodeExecutionRow(Base):
    __tablename__ = "workflow_node_executions"
    __table_args__ = (
        Index("ix_workflow_node_executions_workflow_execution_id", "workflow_execution_id"),
        Index("ix_workflow_node_executions_child_execution_id", "child_execution_id"),
        UniqueConstraint(
            "workflow_execution_id",
            "workflow_node_id",
            name="uq_workflow_node_executions_execution_node",
        ),
        # SQLite's UNIQUE semantics permit multiple NULLs, which gives the
        # required "where non-null" behavior for pending nodes.
        UniqueConstraint("child_task_id", name="uq_workflow_node_executions_child_task_id"),
        UniqueConstraint("child_run_id", name="uq_workflow_node_executions_child_run_id"),
        CheckConstraint(
            "status IN ('pending', 'dispatched', 'succeeded', 'failed', 'cancelled', 'blocked')",
            name="ck_workflow_node_executions_status",
        ),
        CheckConstraint(
            "length(target_agent_revision_sha256) = 64 AND "
            "target_agent_revision_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_workflow_node_executions_sha256",
        ),
        CheckConstraint(
            "(status = 'pending' AND child_task_id IS NULL AND child_run_id IS NULL "
            "AND child_execution_id IS NULL AND started_at IS NULL AND completed_at IS NULL "
            "AND failure_code IS NULL AND failure_message IS NULL) OR "
            "(status = 'dispatched' AND child_task_id IS NOT NULL AND child_run_id IS NOT NULL "
            "AND child_execution_id IS NOT NULL AND started_at IS NOT NULL "
            "AND completed_at IS NULL "
            "AND failure_code IS NULL AND failure_message IS NULL) OR "
            "(status = 'succeeded' AND child_task_id IS NOT NULL AND child_run_id IS NOT NULL "
            "AND child_execution_id IS NOT NULL AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND failure_code IS NULL AND failure_message IS NULL) OR "
            "(status = 'failed' AND child_task_id IS NOT NULL AND child_run_id IS NOT NULL "
            "AND child_execution_id IS NOT NULL AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND failure_code IS NOT NULL "
            "AND failure_message IS NOT NULL) OR "
            "(status = 'cancelled' AND completed_at IS NOT NULL AND failure_code IS NULL "
            "AND failure_message IS NULL AND "
            "((child_task_id IS NULL AND child_run_id IS NULL AND child_execution_id IS NULL "
            "AND started_at IS NULL) OR "
            "(child_task_id IS NOT NULL AND child_run_id IS NOT NULL "
            "AND child_execution_id IS NOT NULL AND started_at IS NOT NULL))) OR "
            "(status = 'blocked' AND completed_at IS NOT NULL AND child_task_id IS NULL "
            "AND child_run_id IS NULL AND child_execution_id IS NULL "
            "AND failure_code IS NOT NULL AND failure_message IS NOT NULL)",
            name="ck_workflow_node_executions_state_shape",
        ),
        ForeignKeyConstraint(
            ["target_agent_id", "target_agent_revision_id"],
            ["agent_revisions.agent_id", "agent_revisions.id"],
            name="fk_workflow_node_executions_revision_ownership",
        ),
        ForeignKeyConstraint(
            ["child_task_id", "target_agent_id"],
            ["task_agent_bindings.task_id", "task_agent_bindings.agent_id"],
            name="fk_workflow_node_executions_child_agent_ownership",
        ),
        # Structural ownership proof: the node must belong to the exact frozen
        # revision recorded by this execution, and that revision must be the
        # one the owning WorkflowExecution froze.
        ForeignKeyConstraint(
            ["workflow_revision_id", "workflow_node_id"],
            ["workflow_nodes.revision_id", "workflow_nodes.id"],
            name="fk_workflow_node_executions_node_revision_ownership",
        ),
        ForeignKeyConstraint(
            ["workflow_execution_id", "workflow_revision_id"],
            ["workflow_executions.id", "workflow_executions.workflow_revision_id"],
            name="fk_workflow_node_executions_execution_revision_ownership",
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    workflow_execution_id: Mapped[str] = mapped_column(ForeignKey("workflow_executions.id"))
    workflow_node_id: Mapped[str] = mapped_column(ForeignKey("workflow_nodes.id"))
    workflow_revision_id: Mapped[str]
    node_key: Mapped[str]
    target_agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    target_agent_revision_id: Mapped[str]
    target_agent_revision_sha256: Mapped[str]
    status: Mapped[str]
    child_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    child_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    child_execution_id: Mapped[str | None]
    result_payload: Mapped[object | None] = mapped_column(JSON)
    failure_code: Mapped[str | None]
    failure_message: Mapped[str | None]
    created_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]


class RunWorkflowResolutionRow(Base):
    __tablename__ = "run_workflow_resolutions"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_run_workflow_resolutions_run_id"),
        CheckConstraint(
            "length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_run_workflow_resolutions_sha256",
        ),
        ForeignKeyConstraint(
            ["workflow_id", "workflow_revision_id"],
            ["workflow_revisions.workflow_id", "workflow_revisions.id"],
            name="fk_run_workflow_resolutions_revision_ownership",
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"))
    workflow_revision_id: Mapped[str]
    content_sha256: Mapped[str]
    resolved_at: Mapped[datetime]


class TaskWorkflowBindingRow(Base):
    __tablename__ = "task_workflow_bindings"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"))
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


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
        # (run_id, id) backs delegation_requests' composite step-ownership FK.
        UniqueConstraint("run_id", "id", name="uq_run_steps_run_id_id"),
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
        Index("ix_approval_requests_subject", "subject_kind", "subject_id"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled', 'expired')",
            name="ck_approval_requests_status",
        ),
        CheckConstraint(
            "category IN ('tool_execution', 'filesystem_write', 'network_access', "
            "'computer_use', 'external_communication', 'other')",
            name="ck_approval_requests_category",
        ),
        CheckConstraint(
            "(subject_kind = 'run' AND run_id IS NOT NULL AND subject_id IS NULL) OR "
            "(subject_kind IN ('skill_promotion', 'skill_rollback') AND run_id IS NULL "
            "AND subject_id IS NOT NULL)",
            name="ck_approval_requests_subject_shape",
        ),
        CheckConstraint(
            "authorization_fingerprint IS NULL OR (length(authorization_fingerprint) = 64 "
            "AND authorization_fingerprint NOT GLOB '*[^0-9a-f]*')",
            name="ck_approval_authorization_fingerprint_hex",
        ),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
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
    subject_kind: Mapped[str] = mapped_column(server_default=text("'run'"))
    subject_id: Mapped[str | None]


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
