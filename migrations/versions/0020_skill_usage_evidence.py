"""Add immutable frozen-skill usage evidence and operator feedback."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_usage_records",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("task_id", sa.String(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("skill_id", sa.String(), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column("revision_id", sa.String(), sa.ForeignKey("skill_revisions.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "resolution_id", sa.String(), sa.ForeignKey("run_skill_resolutions.id"), nullable=False
        ),
        sa.Column("execution_id", sa.String(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("failure_code", sa.String(), nullable=True),
        sa.Column("tool_call_count", sa.Integer(), nullable=False),
        sa.Column("approval_count", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("run_id", "skill_id", name="uq_skill_usage_records_run_skill"),
        sa.CheckConstraint(
            "outcome IN ('succeeded', 'failed', 'cancelled', 'resolution_failed')",
            name="ck_skill_usage_records_outcome",
        ),
        sa.CheckConstraint("attempt_number > 0", name="ck_skill_usage_records_attempt"),
        sa.CheckConstraint("tool_call_count >= 0", name="ck_skill_usage_records_tool_count"),
        sa.CheckConstraint("approval_count >= 0", name="ck_skill_usage_records_approval_count"),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="ck_skill_usage_records_duration"
        ),
    )
    op.create_index("ix_skill_usage_records_run_id", "skill_usage_records", ["run_id"])
    op.create_index("ix_skill_usage_records_skill_id", "skill_usage_records", ["skill_id"])
    op.create_table(
        "skill_run_feedback",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("skill_id", sa.String(), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column("revision_id", sa.String(), sa.ForeignKey("skill_revisions.id"), nullable=False),
        sa.Column("rating", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "rating IN ('helpful', 'neutral', 'harmful')", name="ck_skill_feedback_rating"
        ),
        sa.CheckConstraint(
            "length(created_by) BETWEEN 1 AND 128", name="ck_skill_feedback_creator"
        ),
    )
    op.create_index("ix_skill_run_feedback_run_id", "skill_run_feedback", ["run_id"])
    op.create_index("ix_skill_run_feedback_skill_id", "skill_run_feedback", ["skill_id"])


def downgrade() -> None:
    op.drop_index("ix_skill_run_feedback_skill_id", table_name="skill_run_feedback")
    op.drop_index("ix_skill_run_feedback_run_id", table_name="skill_run_feedback")
    op.drop_table("skill_run_feedback")
    op.drop_index("ix_skill_usage_records_skill_id", table_name="skill_usage_records")
    op.drop_index("ix_skill_usage_records_run_id", table_name="skill_usage_records")
    op.drop_table("skill_usage_records")
