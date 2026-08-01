"""Add immutable deterministic skill evaluation substrate."""

# ruff: noqa: E501

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_evaluation_suites",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("skill_id", sa.String(), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("skill_id", "name", name="uq_skill_evaluation_suite_name"),
    )
    op.create_table(
        "skill_evaluation_cases",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "suite_id", sa.String(), sa.ForeignKey("skill_evaluation_suites.id"), nullable=False
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("input", sa.String(), nullable=False),
        sa.Column("expected_properties", sa.JSON(), nullable=False),
        sa.Column("grading_kind", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("suite_id", "position", name="uq_skill_evaluation_case_position"),
        sa.CheckConstraint("position > 0", name="ck_skill_evaluation_case_position"),
    )
    op.create_table(
        "skill_evaluation_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "suite_id", sa.String(), sa.ForeignKey("skill_evaluation_suites.id"), nullable=False
        ),
        sa.Column("skill_id", sa.String(), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column("revision_id", sa.String(), sa.ForeignKey("skill_revisions.id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("evaluator_version", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=False),
        sa.Column("aggregate_result", sa.JSON(), nullable=False),
        sa.Column("suite_snapshot", sa.JSON(), nullable=False),
        sa.Column("runtime_fingerprint", sa.String(), nullable=False),
    )
    op.create_table(
        "skill_evaluation_case_results",
        sa.Column(
            "evaluation_run_id",
            sa.String(),
            sa.ForeignKey("skill_evaluation_runs.id"),
            primary_key=True,
        ),
        sa.Column(
            "case_id", sa.String(), sa.ForeignKey("skill_evaluation_cases.id"), primary_key=True
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reason_code", sa.String(), nullable=True),
        sa.Column("bounded_details", sa.String(), nullable=False),
        sa.Column("output_sha256", sa.String(), nullable=False),
        sa.UniqueConstraint("evaluation_run_id", "case_id", name="uq_skill_evaluation_case_result"),
        sa.CheckConstraint("score BETWEEN 0 AND 1", name="ck_skill_evaluation_case_score"),
    )


def downgrade() -> None:
    op.drop_table("skill_evaluation_case_results")
    op.drop_table("skill_evaluation_runs")
    op.drop_table("skill_evaluation_cases")
    op.drop_table("skill_evaluation_suites")
