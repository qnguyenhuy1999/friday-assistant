"""Close the Phase 20 schema in one restart-safe final migration.

The earlier Phase 20 revisions remain useful upgrade checkpoints.  This
revision is the owned final head: it adds the cross-row ownership fences,
canonical approval subjects, immutable provenance, and the non-null evidence
link without inventing authority for incompatible historical rows.
"""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def _count(sql: str) -> int:
    return int(op.get_bind().scalar(sa.text(sql)) or 0)


def _fail_if(sql: str, message: str) -> None:
    if _count(sql):
        raise RuntimeError(message)


def upgrade() -> None:
    # These rows predate the authority model.  Guessing an approval or an
    # evidence snapshot would fabricate provenance, so the upgrade stops before
    # changing anything when incompatible data is present.
    _fail_if(
        "SELECT count(*) FROM skill_revisions WHERE source_kind = 'generated'",
        "0029 cannot upgrade generated revisions without promotion provenance",
    )
    _fail_if(
        "SELECT count(*) FROM skill_improvement_proposals WHERE evidence_snapshot_id IS NULL",
        "0029 cannot upgrade proposals without immutable evidence snapshots",
    )
    _fail_if(
        "SELECT count(*) FROM skill_promotion_requests",
        "0029 cannot upgrade historical promotions without canonical approvals",
    )
    _fail_if(
        "SELECT count(*) FROM skill_rollback_requests",
        "0029 cannot upgrade historical rollbacks without canonical approvals",
    )
    _fail_if(
        "SELECT count(*) FROM skill_promotion_requests WHERE approval_request_id IS NULL",
        "0029 cannot upgrade promotions without canonical approvals",
    ) if _column_exists("skill_promotion_requests", "approval_request_id") else None
    _fail_if(
        "SELECT count(*) FROM skill_rollback_requests WHERE approval_request_id IS NULL",
        "0029 cannot upgrade rollbacks without canonical approvals",
    ) if _column_exists("skill_rollback_requests", "approval_request_id") else None

    with op.batch_alter_table("runs") as batch:
        batch.create_unique_constraint("uq_runs_id_task", ["id", "task_id"])
        batch.create_unique_constraint("uq_runs_id_execution", ["id", "execution_id"])
        batch.create_check_constraint(
            "ck_runs_status",
            "status IN ('queued', 'running', 'waiting_for_approval', 'succeeded', 'failed', "
            "'cancelled')",
        )

    with op.batch_alter_table("approval_requests") as batch:
        batch.alter_column("run_id", existing_type=sa.String(), nullable=True)
        batch.add_column(
            sa.Column("subject_kind", sa.String(), nullable=False, server_default="run")
        )
        batch.add_column(sa.Column("subject_id", sa.String(), nullable=True))
        batch.create_index(
            "ix_approval_requests_subject", ["subject_kind", "subject_id"], unique=False
        )
        batch.create_check_constraint(
            "ck_approval_requests_status",
            "status IN ('pending', 'approved', 'rejected', 'cancelled', 'expired')",
        )
        batch.create_check_constraint(
            "ck_approval_requests_category",
            "category IN ('tool_execution', 'filesystem_write', 'network_access', "
            "'computer_use', 'external_communication', 'other')",
        )
        batch.create_check_constraint(
            "ck_approval_requests_subject_shape",
            "(subject_kind = 'run' AND run_id IS NOT NULL AND subject_id IS NULL) OR "
            "(subject_kind IN ('skill_promotion', 'skill_rollback') AND run_id IS NULL "
            "AND subject_id IS NOT NULL)",
        )

    with op.batch_alter_table("skill_revisions") as batch:
        batch.add_column(sa.Column("promotion_request_id", sa.String(), nullable=True))
        batch.create_foreign_key(
            "fk_skill_revisions_promotion_request",
            "skill_promotion_requests",
            ["promotion_request_id"],
            ["id"],
        )
        batch.create_unique_constraint(
            "uq_skill_revisions_promotion_request", ["promotion_request_id"]
        )
        batch.create_check_constraint(
            "ck_skill_revisions_promotion_provenance",
            "(source_kind = 'generated' AND promotion_request_id IS NOT NULL) OR "
            "(source_kind IN ('operator', 'imported') AND promotion_request_id IS NULL)",
        )

    with op.batch_alter_table("run_skill_resolutions") as batch:
        batch.create_unique_constraint("uq_run_skill_resolutions_run_id_id", ["run_id", "id"])

    with op.batch_alter_table("run_skill_bindings") as batch:
        batch.create_unique_constraint(
            "uq_run_skill_bindings_frozen_revision", ["run_id", "skill_id", "revision_id"]
        )

    with op.batch_alter_table("skill_usage_records") as batch:
        batch.create_foreign_key(
            "fk_skill_usage_records_frozen_binding",
            "run_skill_bindings",
            ["run_id", "skill_id", "revision_id"],
            ["run_id", "skill_id", "revision_id"],
        )
        batch.create_foreign_key(
            "fk_skill_usage_records_resolution_ownership",
            "run_skill_resolutions",
            ["run_id", "resolution_id"],
            ["run_id", "id"],
        )
        batch.create_foreign_key(
            "fk_skill_usage_records_task_ownership",
            "runs",
            ["run_id", "task_id"],
            ["id", "task_id"],
        )
        batch.create_foreign_key(
            "fk_skill_usage_records_execution_ownership",
            "runs",
            ["run_id", "execution_id"],
            ["id", "execution_id"],
        )
        batch.create_check_constraint(
            "ck_skill_usage_records_position", "position BETWEEN 1 AND 16"
        )
        batch.create_check_constraint(
            "ck_skill_usage_records_failure_code_shape",
            "(outcome = 'failed' AND failure_code IS NOT NULL AND "
            "length(failure_code) BETWEEN 1 AND 128 AND failure_code NOT GLOB '*[^a-z0-9_]*') OR "
            "(outcome <> 'failed' AND failure_code IS NULL)",
        )

    with op.batch_alter_table("skill_run_feedback") as batch:
        batch.create_foreign_key(
            "fk_skill_feedback_frozen_binding",
            "run_skill_bindings",
            ["run_id", "skill_id", "revision_id"],
            ["run_id", "skill_id", "revision_id"],
        )

    with op.batch_alter_table("skill_evaluation_suites") as batch:
        batch.create_unique_constraint("uq_skill_evaluation_suites_skill_id", ["skill_id", "id"])
        batch.create_check_constraint(
            "ck_skill_evaluation_suites_status", "status IN ('active', 'disabled')"
        )
        batch.create_check_constraint(
            "ck_skill_evaluation_suites_name", "length(name) BETWEEN 1 AND 256"
        )

    with op.batch_alter_table("skill_evaluation_cases") as batch:
        batch.create_check_constraint("ck_skill_evaluation_case_position", "position > 0")
        batch.create_check_constraint(
            "ck_skill_evaluation_case_input", "length(input) BETWEEN 1 AND 32000"
        )
        batch.create_check_constraint(
            "ck_skill_evaluation_case_grading_kind",
            "grading_kind IN ('exact_match', 'contains_all', 'contains_none', "
            "'json_schema', 'required_keys', 'tool_proposal_shape')",
        )

    with op.batch_alter_table("skill_evidence_snapshots") as batch:
        batch.create_unique_constraint("uq_skill_evidence_snapshots_skill_id", ["skill_id", "id"])
        batch.create_unique_constraint(
            "uq_skill_evidence_snapshot_ownership", ["skill_id", "base_revision_id", "id"]
        )
        batch.create_foreign_key(
            "fk_skill_evidence_snapshots_base_revision_ownership",
            "skill_revisions",
            ["skill_id", "base_revision_id"],
            ["skill_id", "id"],
        )
        batch.create_check_constraint(
            "ck_skill_evidence_snapshot_sha256",
            "length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'",
        )

    with op.batch_alter_table("skill_improvement_proposals") as batch:
        batch.create_unique_constraint(
            "uq_skill_improvement_proposals_skill_id", ["skill_id", "id"]
        )
        batch.create_foreign_key(
            "fk_skill_improvement_proposals_base_revision_ownership",
            "skill_revisions",
            ["skill_id", "base_revision_id"],
            ["skill_id", "id"],
        )
        batch.create_foreign_key(
            "fk_skill_improvement_proposals_evidence_snapshot_ownership",
            "skill_evidence_snapshots",
            ["skill_id", "evidence_snapshot_id"],
            ["skill_id", "id"],
        )
        batch.alter_column("evidence_snapshot_id", existing_type=sa.String(), nullable=False)
        batch.create_check_constraint(
            "ck_skill_improvement_proposals_status",
            "status IN ('draft', 'ready_for_evaluation', 'evaluating', 'ready_for_review', "
            "'approved', 'rejected', 'superseded', 'cancelled', 'expired', 'promoted')",
        )
        batch.create_check_constraint(
            "ck_skill_improvement_proposals_evidence_hash",
            "length(evidence_snapshot_hash) = 64 AND evidence_snapshot_hash NOT GLOB '*[^0-9a-f]*'",
        )
        batch.create_check_constraint(
            "ck_skill_improvement_proposals_content_hash",
            "length(proposed_content_sha256) = 64 AND proposed_content_sha256 NOT GLOB '*[^0-9a-f]*'",
        )
    op.create_index(
        "uq_skill_improvement_proposals_one_open",
        "skill_improvement_proposals",
        ["skill_id"],
        unique=True,
        sqlite_where=sa.text(
            "status IN ('draft', 'ready_for_evaluation', 'evaluating', 'ready_for_review', 'approved')"
        ),
    )

    bind = op.get_bind()
    # Freeze the target digest and runtime metadata for rows created by the
    # earlier checkpoints before making the new fields mandatory.
    with op.batch_alter_table("skill_evaluation_runs") as batch:
        batch.add_column(sa.Column("target_content_sha256", sa.String(), nullable=True))
        batch.add_column(
            sa.Column("runtime_metadata", sa.JSON(), nullable=False, server_default="{}")
        )
        batch.create_unique_constraint("uq_skill_evaluation_runs_skill_id", ["skill_id", "id"])
        batch.create_foreign_key(
            "fk_skill_evaluation_runs_suite_ownership",
            "skill_evaluation_suites",
            ["skill_id", "suite_id"],
            ["skill_id", "id"],
        )
        batch.create_foreign_key(
            "fk_skill_evaluation_runs_revision_ownership",
            "skill_revisions",
            ["skill_id", "revision_id"],
            ["skill_id", "id"],
        )
        batch.create_foreign_key(
            "fk_skill_evaluation_runs_proposal_ownership",
            "skill_improvement_proposals",
            ["skill_id", "proposal_id"],
            ["skill_id", "id"],
        )
    bind.execute(
        sa.text(
            "UPDATE skill_evaluation_runs SET target_content_sha256 = "
            "(SELECT content_sha256 FROM skill_revisions WHERE skill_revisions.id = "
            "skill_evaluation_runs.revision_id) WHERE revision_id IS NOT NULL"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE skill_evaluation_runs SET target_content_sha256 = "
            "(SELECT proposed_content_sha256 FROM skill_improvement_proposals WHERE "
            "skill_improvement_proposals.id = skill_evaluation_runs.proposal_id) "
            "WHERE proposal_id IS NOT NULL"
        )
    )
    _fail_if(
        "SELECT count(*) FROM skill_evaluation_runs WHERE target_content_sha256 IS NULL",
        "0029 could not establish evaluation target integrity",
    )
    with op.batch_alter_table("skill_evaluation_runs") as batch:
        batch.alter_column("target_content_sha256", existing_type=sa.String(), nullable=False)
        batch.alter_column("runtime_metadata", existing_type=sa.JSON(), server_default=None)
        batch.create_check_constraint(
            "ck_skill_evaluation_runs_target_xor",
            "(revision_id IS NOT NULL AND proposal_id IS NULL) OR "
            "(revision_id IS NULL AND proposal_id IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_skill_evaluation_runs_status", "status IN ('succeeded', 'failed')"
        )
        batch.create_check_constraint(
            "ck_skill_evaluation_runs_runtime_fingerprint",
            "length(runtime_fingerprint) = 64 AND runtime_fingerprint NOT GLOB '*[^0-9a-f]*'",
        )
        batch.create_check_constraint(
            "ck_skill_evaluation_runs_target_sha256",
            "length(target_content_sha256) = 64 AND target_content_sha256 NOT GLOB '*[^0-9a-f]*'",
        )

    with op.batch_alter_table("skill_evaluation_case_results") as batch:
        batch.create_check_constraint(
            "ck_skill_evaluation_case_status", "status IN ('succeeded', 'failed')"
        )
        batch.create_check_constraint(
            "ck_skill_evaluation_case_output_sha256",
            "length(output_sha256) = 64 AND output_sha256 NOT GLOB '*[^0-9a-f]*'",
        )

    # The report shape was already code-owned in 0023.  Reconstruct it from
    # immutable columns so old comparison rows remain verifiable, rather than
    # dropping or replacing their report hash.
    with op.batch_alter_table("skill_candidate_evaluations") as batch:
        batch.add_column(sa.Column("comparison_report", sa.JSON(), nullable=True))
    rows = (
        bind.execute(
            sa.text(
                "SELECT id, proposal_id, baseline_evaluation_run_id, candidate_evaluation_run_id, "
                "comparison_policy_version, result, recommendation, score_delta, regression_count, "
                "improvement_count, inconclusive_count, report_sha256 FROM skill_candidate_evaluations"
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        report = {
            "proposal_id": row["proposal_id"],
            "baseline_run_id": row["baseline_evaluation_run_id"],
            "candidate_run_id": row["candidate_evaluation_run_id"],
            "runtime_fingerprint": bind.execute(
                sa.text("SELECT runtime_fingerprint FROM skill_evaluation_runs WHERE id = :id"),
                {"id": row["baseline_evaluation_run_id"]},
            ).scalar_one(),
            "score_delta": row["score_delta"],
            "regression_count": row["regression_count"],
            "improvement_count": row["improvement_count"],
            "inconclusive_count": row["inconclusive_count"],
            "result": row["result"],
            "recommendation": row["recommendation"],
            "comparison_policy_version": row["comparison_policy_version"],
        }
        report_hash = hashlib.sha256(
            json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if report_hash != row["report_sha256"]:
            raise RuntimeError("0029 found an unverifiable historical comparison report")
        bind.execute(
            sa.text(
                "UPDATE skill_candidate_evaluations SET comparison_report = :report WHERE id = :id"
            ),
            {"report": json.dumps(report, separators=(",", ":")), "id": row["id"]},
        )
    with op.batch_alter_table("skill_candidate_evaluations") as batch:
        batch.create_unique_constraint(
            "uq_skill_candidate_evaluations_proposal_id", ["proposal_id", "id"]
        )
        batch.alter_column("comparison_report", existing_type=sa.JSON(), nullable=False)
        batch.create_check_constraint(
            "ck_skill_candidate_evaluations_result",
            "result IN ('better', 'worse', 'equivalent', 'mixed', 'inconclusive')",
        )
        batch.create_check_constraint(
            "ck_skill_candidate_evaluations_recommendation",
            "recommendation IN ('eligible', 'not_eligible', 'requires_manual_override')",
        )
        batch.create_check_constraint(
            "ck_skill_candidate_evaluations_counts",
            "regression_count >= 0 AND improvement_count >= 0 AND inconclusive_count >= 0",
        )
        batch.create_check_constraint(
            "ck_skill_candidate_evaluations_report_sha256",
            "length(report_sha256) = 64 AND report_sha256 NOT GLOB '*[^0-9a-f]*'",
        )

    with op.batch_alter_table("skill_promotion_requests") as batch:
        batch.add_column(sa.Column("approval_request_id", sa.String(), nullable=True))
        batch.create_foreign_key(
            "fk_skill_promotion_approval_request",
            "approval_requests",
            ["approval_request_id"],
            ["id"],
        )
        batch.create_unique_constraint(
            "uq_skill_promotion_approval_request", ["approval_request_id"]
        )
        batch.create_foreign_key(
            "fk_skill_promotion_proposal_ownership",
            "skill_improvement_proposals",
            ["skill_id", "proposal_id"],
            ["skill_id", "id"],
        )
        batch.create_foreign_key(
            "fk_skill_promotion_candidate_evaluation_ownership",
            "skill_candidate_evaluations",
            ["proposal_id", "candidate_evaluation_id"],
            ["proposal_id", "id"],
        )
        batch.create_foreign_key(
            "fk_skill_promotion_base_revision_ownership",
            "skill_revisions",
            ["skill_id", "base_revision_id"],
            ["skill_id", "id"],
        )
        batch.create_foreign_key(
            "fk_skill_promotion_expected_active_ownership",
            "skill_revisions",
            ["skill_id", "expected_active_revision_id"],
            ["skill_id", "id"],
        )
        batch.create_check_constraint(
            "ck_skill_promotion_status",
            "status IN ('pending', 'approved', 'rejected', 'stale', 'cancelled', 'promoted')",
        )
        batch.create_check_constraint(
            "ck_skill_promotion_candidate_sha256",
            "length(candidate_sha256) = 64 AND candidate_sha256 NOT GLOB '*[^0-9a-f]*'",
        )
        batch.create_check_constraint(
            "ck_skill_promotion_report_sha256",
            "length(comparison_report_sha256) = 64 AND comparison_report_sha256 NOT GLOB '*[^0-9a-f]*'",
        )
        batch.create_check_constraint(
            "ck_skill_promotion_authorization_fingerprint",
            "length(authorization_fingerprint) = 64 AND authorization_fingerprint NOT GLOB '*[^0-9a-f]*'",
        )
        batch.alter_column("approval_request_id", existing_type=sa.String(), nullable=False)

    with op.batch_alter_table("skill_rollback_requests") as batch:
        batch.add_column(sa.Column("approval_request_id", sa.String(), nullable=True))
        batch.create_foreign_key(
            "fk_skill_rollback_approval_request",
            "approval_requests",
            ["approval_request_id"],
            ["id"],
        )
        batch.create_unique_constraint(
            "uq_skill_rollback_approval_request", ["approval_request_id"]
        )
        batch.create_foreign_key(
            "fk_skill_rollback_current_revision_ownership",
            "skill_revisions",
            ["skill_id", "expected_current_revision_id"],
            ["skill_id", "id"],
        )
        batch.create_foreign_key(
            "fk_skill_rollback_target_revision_ownership",
            "skill_revisions",
            ["skill_id", "target_revision_id"],
            ["skill_id", "id"],
        )
        batch.create_check_constraint(
            "ck_skill_rollback_status",
            "status IN ('pending', 'approved', 'rejected', 'stale', 'cancelled', 'completed')",
        )
        batch.create_check_constraint(
            "ck_skill_rollback_authorization_fingerprint",
            "length(authorization_fingerprint) = 64 AND authorization_fingerprint NOT GLOB '*[^0-9a-f]*'",
        )
        batch.create_check_constraint(
            "ck_skill_rollback_target_not_current",
            "target_revision_id <> expected_current_revision_id",
        )
        batch.alter_column("approval_request_id", existing_type=sa.String(), nullable=False)

    with op.batch_alter_table("skill_improvement_policies") as batch:
        batch.create_check_constraint(
            "ck_skill_improvement_policy_thresholds",
            "minimum_usage_records >= 0 AND minimum_failures >= 0 AND "
            "minimum_harmful_feedback >= 0",
        )
        batch.create_check_constraint(
            "ck_skill_improvement_policy_cooldown", "cooldown_seconds >= 0"
        )
        batch.create_check_constraint(
            "ck_skill_improvement_policy_bounds",
            "max_open_proposals = 1 AND evidence_window_size BETWEEN 1 AND 200",
        )

    op.create_table(
        "skill_improvement_work_items",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("skill_id", sa.String(), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column(
            "proposal_id",
            sa.String(),
            sa.ForeignKey("skill_improvement_proposals.id"),
            nullable=True,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("claimed_by", sa.String(), nullable=True),
        sa.Column("claim_token", sa.String(), nullable=True),
        sa.Column("claim_generation", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("failure_code", sa.String(), nullable=True),
        sa.Column("failure_detail", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('evidence_selection', 'candidate_generation', 'baseline_evaluation', "
            "'candidate_evaluation', 'comparison', 'ready_for_review', 'failed', 'complete')",
            name="ck_skill_improvement_work_state",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_skill_improvement_work_attempt_count"),
        sa.CheckConstraint(
            "claim_generation >= 0", name="ck_skill_improvement_work_claim_generation"
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR (length(failure_code) BETWEEN 1 AND 128 "
            "AND failure_code NOT GLOB '*[^a-z0-9_]*')",
            name="ck_skill_improvement_work_failure_code",
        ),
        sa.CheckConstraint(
            "(claimed_by IS NULL AND claim_token IS NULL AND lease_expires_at IS NULL) OR "
            "(claimed_by IS NOT NULL AND claim_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_skill_improvement_work_claim_shape",
        ),
        sa.CheckConstraint(
            "failure_detail IS NULL OR length(failure_detail) BETWEEN 1 AND 512",
            name="ck_skill_improvement_work_failure_detail",
        ),
    )
    op.create_index(
        "uq_skill_improvement_work_active_skill",
        "skill_improvement_work_items",
        ["skill_id"],
        unique=True,
        sqlite_where=sa.text(
            "state IN ('evidence_selection', 'candidate_generation', 'baseline_evaluation', "
            "'candidate_evaluation', 'comparison', 'failed')"
        ),
    )

    # SQLite cannot express these cross-row lifecycle rules as CHECK
    # constraints.  Keep them in the final owned migration so raw SQL cannot
    # manufacture a generated revision or repoint a Skill around the approval
    # lane.  The application transaction stages the promotion row before the
    # active-pointer update, and consumes the approval before commit.
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(
            """
            CREATE TRIGGER ck_generated_revision_canonical_approval
            BEFORE INSERT ON skill_revisions
            FOR EACH ROW
            WHEN NEW.source_kind = 'generated'
             AND NOT EXISTS (
                SELECT 1
                FROM skill_promotion_requests p
                JOIN approval_requests a ON a.id = p.approval_request_id
                WHERE p.id = NEW.promotion_request_id
                  AND p.skill_id = NEW.skill_id
                  AND a.subject_kind = 'skill_promotion'
                  AND a.subject_id = p.id
                  AND a.authorization_fingerprint = p.authorization_fingerprint
                  AND (
                    (p.status IN ('pending', 'approved')
                     AND a.status = 'approved' AND a.consumed_at IS NULL)
                    OR
                    (p.status = 'promoted'
                     AND p.promoted_revision_id = NEW.id
                     AND a.status = 'approved' AND a.consumed_at IS NOT NULL)
                  )
             )
            BEGIN
                SELECT RAISE(ABORT, 'generated revision requires canonical approval');
            END
            """
        )
        bind.exec_driver_sql(
            """
            CREATE TRIGGER ck_promoted_revision_success
            AFTER UPDATE OF status, promoted_revision_id ON skill_promotion_requests
            FOR EACH ROW
            WHEN NEW.status = 'promoted'
             AND NOT EXISTS (
                SELECT 1
                FROM skill_revisions r
                WHERE r.id = NEW.promoted_revision_id
                  AND r.skill_id = NEW.skill_id
                  AND r.source_kind = 'generated'
                  AND r.promotion_request_id = NEW.id
             )
            BEGIN
                SELECT RAISE(ABORT, 'promoted request requires generated revision provenance');
            END
            """
        )
        bind.exec_driver_sql(
            """
            CREATE TRIGGER ck_consumed_skill_approval_success
            AFTER UPDATE OF consumed_at ON approval_requests
            FOR EACH ROW
            WHEN NEW.subject_kind = 'skill_promotion'
             AND NEW.consumed_at IS NOT NULL
             AND NOT EXISTS (
                SELECT 1
                FROM skill_promotion_requests p
                JOIN skill_revisions r ON r.id = p.promoted_revision_id
                WHERE p.id = NEW.subject_id
                  AND p.approval_request_id = NEW.id
                  AND p.status = 'promoted'
                  AND r.source_kind = 'generated'
                  AND r.promotion_request_id = p.id
             )
            BEGIN
                SELECT RAISE(ABORT, 'consumed promotion approval lacks successful promotion');
            END
            """
        )
        bind.exec_driver_sql(
            """
            CREATE TRIGGER ck_skill_active_pointer_authority
            BEFORE UPDATE OF active_revision_id ON skills
            FOR EACH ROW
            WHEN NEW.active_revision_id IS NOT OLD.active_revision_id
             AND NEW.active_revision_id IS NOT NULL
             AND (
                (
                    EXISTS (
                        SELECT 1 FROM skill_revisions r
                        WHERE r.id = NEW.active_revision_id
                          AND r.skill_id = NEW.id
                          AND r.source_kind = 'generated'
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM skill_promotion_requests p
                        WHERE p.skill_id = NEW.id
                          AND p.promoted_revision_id = NEW.active_revision_id
                          AND p.status = 'promoted'
                    )
                )
                OR
                (
                    OLD.active_revision_id IS NOT NULL
                    AND (
                        SELECT r.version FROM skill_revisions r
                        WHERE r.id = NEW.active_revision_id
                          AND r.skill_id = NEW.id
                    ) < (
                        SELECT r.version FROM skill_revisions r
                        WHERE r.id = OLD.active_revision_id
                          AND r.skill_id = NEW.id
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM skill_rollback_requests rb
                        JOIN approval_requests a ON a.id = rb.approval_request_id
                        WHERE rb.skill_id = NEW.id
                          AND rb.target_revision_id = NEW.active_revision_id
                          AND rb.expected_current_revision_id = OLD.active_revision_id
                          AND a.subject_kind = 'skill_rollback'
                          AND a.subject_id = rb.id
                          AND a.authorization_fingerprint = rb.authorization_fingerprint
                          AND (
                            (rb.status IN ('pending', 'approved')
                             AND a.status = 'approved' AND a.consumed_at IS NULL)
                            OR
                            (rb.status = 'completed'
                             AND a.status = 'approved' AND a.consumed_at IS NOT NULL)
                          )
                    )
                )
             )
            BEGIN
                SELECT RAISE(ABORT, 'active revision change requires approved promotion or rollback');
            END
            """
        )


def _column_exists(table: str, column: str) -> bool:
    return column in {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        for trigger in (
            "ck_skill_active_pointer_authority",
            "ck_consumed_skill_approval_success",
            "ck_promoted_revision_success",
            "ck_generated_revision_canonical_approval",
        ):
            bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")
    op.drop_index(
        "uq_skill_improvement_work_active_skill", table_name="skill_improvement_work_items"
    )
    op.drop_table("skill_improvement_work_items")
    with op.batch_alter_table("skill_rollback_requests") as batch:
        for name in (
            "ck_skill_rollback_target_not_current",
            "ck_skill_rollback_authorization_fingerprint",
            "ck_skill_rollback_status",
        ):
            batch.drop_constraint(name, type_="check")
        for name in (
            "fk_skill_rollback_target_revision_ownership",
            "fk_skill_rollback_current_revision_ownership",
            "fk_skill_rollback_approval_request",
        ):
            batch.drop_constraint(name, type_="foreignkey")
        batch.drop_constraint("uq_skill_rollback_approval_request", type_="unique")
        batch.drop_column("approval_request_id")
    with op.batch_alter_table("skill_promotion_requests") as batch:
        for name in (
            "ck_skill_promotion_authorization_fingerprint",
            "ck_skill_promotion_report_sha256",
            "ck_skill_promotion_candidate_sha256",
            "ck_skill_promotion_status",
        ):
            batch.drop_constraint(name, type_="check")
        for name in (
            "fk_skill_promotion_candidate_evaluation_ownership",
            "fk_skill_promotion_proposal_ownership",
            "fk_skill_promotion_expected_active_ownership",
            "fk_skill_promotion_base_revision_ownership",
            "fk_skill_promotion_approval_request",
        ):
            batch.drop_constraint(name, type_="foreignkey")
        batch.drop_constraint("uq_skill_promotion_approval_request", type_="unique")
        batch.drop_column("approval_request_id")
    with op.batch_alter_table("skill_candidate_evaluations") as batch:
        for name in (
            "ck_skill_candidate_evaluations_report_sha256",
            "ck_skill_candidate_evaluations_counts",
            "ck_skill_candidate_evaluations_recommendation",
            "ck_skill_candidate_evaluations_result",
        ):
            batch.drop_constraint(name, type_="check")
        batch.drop_constraint("uq_skill_candidate_evaluations_proposal_id", type_="unique")
        batch.drop_column("comparison_report")
    with op.batch_alter_table("skill_improvement_proposals") as batch:
        for name in (
            "ck_skill_improvement_proposals_content_hash",
            "ck_skill_improvement_proposals_evidence_hash",
            "ck_skill_improvement_proposals_status",
        ):
            batch.drop_constraint(name, type_="check")
        for name in (
            "fk_skill_improvement_proposals_evidence_snapshot_ownership",
            "fk_skill_improvement_proposals_base_revision_ownership",
        ):
            batch.drop_constraint(name, type_="foreignkey")
        batch.drop_constraint("uq_skill_improvement_proposals_skill_id", type_="unique")
        batch.alter_column("evidence_snapshot_id", existing_type=sa.String(), nullable=True)
    op.drop_index(
        "uq_skill_improvement_proposals_one_open", table_name="skill_improvement_proposals"
    )
    with op.batch_alter_table("skill_evaluation_case_results") as batch:
        batch.drop_constraint("ck_skill_evaluation_case_output_sha256", type_="check")
        batch.drop_constraint("ck_skill_evaluation_case_status", type_="check")
    with op.batch_alter_table("skill_evaluation_runs") as batch:
        for name in (
            "ck_skill_evaluation_runs_target_sha256",
            "ck_skill_evaluation_runs_runtime_fingerprint",
            "ck_skill_evaluation_runs_status",
            "ck_skill_evaluation_runs_target_xor",
        ):
            batch.drop_constraint(name, type_="check")
        for name in (
            "fk_skill_evaluation_runs_proposal_ownership",
            "fk_skill_evaluation_runs_revision_ownership",
            "fk_skill_evaluation_runs_suite_ownership",
        ):
            batch.drop_constraint(name, type_="foreignkey")
        batch.drop_constraint("uq_skill_evaluation_runs_skill_id", type_="unique")
        batch.drop_column("runtime_metadata")
        batch.drop_column("target_content_sha256")
    with op.batch_alter_table("skill_evaluation_cases") as batch:
        batch.drop_constraint("ck_skill_evaluation_case_position", type_="check")
        batch.drop_constraint("ck_skill_evaluation_case_grading_kind", type_="check")
        batch.drop_constraint("ck_skill_evaluation_case_input", type_="check")
    with op.batch_alter_table("skill_evaluation_suites") as batch:
        batch.drop_constraint("ck_skill_evaluation_suites_name", type_="check")
        batch.drop_constraint("ck_skill_evaluation_suites_status", type_="check")
        batch.drop_constraint("uq_skill_evaluation_suites_skill_id", type_="unique")
    with op.batch_alter_table("skill_evidence_snapshots") as batch:
        batch.drop_constraint("ck_skill_evidence_snapshot_sha256", type_="check")
        batch.drop_constraint(
            "fk_skill_evidence_snapshots_base_revision_ownership", type_="foreignkey"
        )
        batch.drop_constraint("uq_skill_evidence_snapshot_ownership", type_="unique")
        batch.drop_constraint("uq_skill_evidence_snapshots_skill_id", type_="unique")
    with op.batch_alter_table("skill_run_feedback") as batch:
        batch.drop_constraint("fk_skill_feedback_frozen_binding", type_="foreignkey")
    with op.batch_alter_table("skill_usage_records") as batch:
        batch.drop_constraint("ck_skill_usage_records_failure_code_shape", type_="check")
        batch.drop_constraint("ck_skill_usage_records_position", type_="check")
        batch.drop_constraint("fk_skill_usage_records_execution_ownership", type_="foreignkey")
        batch.drop_constraint("fk_skill_usage_records_task_ownership", type_="foreignkey")
        batch.drop_constraint("fk_skill_usage_records_resolution_ownership", type_="foreignkey")
        batch.drop_constraint("fk_skill_usage_records_frozen_binding", type_="foreignkey")
    with op.batch_alter_table("run_skill_bindings") as batch:
        batch.drop_constraint("uq_run_skill_bindings_frozen_revision", type_="unique")
    with op.batch_alter_table("run_skill_resolutions") as batch:
        batch.drop_constraint("uq_run_skill_resolutions_run_id_id", type_="unique")
    with op.batch_alter_table("skill_revisions") as batch:
        batch.drop_constraint("ck_skill_revisions_promotion_provenance", type_="check")
        batch.drop_constraint("uq_skill_revisions_promotion_request", type_="unique")
        batch.drop_constraint("fk_skill_revisions_promotion_request", type_="foreignkey")
        batch.drop_column("promotion_request_id")
    with op.batch_alter_table("skill_improvement_policies") as batch:
        batch.drop_constraint("ck_skill_improvement_policy_bounds", type_="check")
        batch.drop_constraint("ck_skill_improvement_policy_cooldown", type_="check")
        batch.drop_constraint("ck_skill_improvement_policy_thresholds", type_="check")
    with op.batch_alter_table("runs") as batch:
        batch.drop_constraint("ck_runs_status", type_="check")
        batch.drop_constraint("uq_runs_id_execution", type_="unique")
        batch.drop_constraint("uq_runs_id_task", type_="unique")
    with op.batch_alter_table("approval_requests") as batch:
        batch.drop_constraint("ck_approval_requests_subject_shape", type_="check")
        batch.drop_constraint("ck_approval_requests_category", type_="check")
        batch.drop_constraint("ck_approval_requests_status", type_="check")
        batch.drop_index("ix_approval_requests_subject")
        batch.drop_column("subject_id")
        batch.drop_column("subject_kind")
        batch.alter_column("run_id", existing_type=sa.String(), nullable=False)
