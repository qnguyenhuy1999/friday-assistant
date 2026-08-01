"""Allow isolated proposal evaluation and persist immutable comparisons."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("skill_evaluation_runs") as batch:
        batch.alter_column("revision_id", existing_type=sa.String(), nullable=True)
        batch.add_column(
            sa.Column(
                "proposal_id",
                sa.String(),
                nullable=True,
            )
        )
        batch.create_foreign_key(
            "fk_skill_evaluation_runs_proposal_id",
            "skill_improvement_proposals",
            ["proposal_id"],
            ["id"],
        )
    op.create_index(
        "ix_skill_evaluation_runs_proposal_id", "skill_evaluation_runs", ["proposal_id"]
    )
    op.create_table(
        "skill_candidate_evaluations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "proposal_id",
            sa.String(),
            sa.ForeignKey("skill_improvement_proposals.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "baseline_evaluation_run_id",
            sa.String(),
            sa.ForeignKey("skill_evaluation_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "candidate_evaluation_run_id",
            sa.String(),
            sa.ForeignKey("skill_evaluation_runs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("comparison_policy_version", sa.String(), nullable=False),
        sa.Column("result", sa.String(), nullable=False),
        sa.Column("recommendation", sa.String(), nullable=False),
        sa.Column("score_delta", sa.Float(), nullable=False),
        sa.Column("regression_count", sa.Integer(), nullable=False),
        sa.Column("improvement_count", sa.Integer(), nullable=False),
        sa.Column("inconclusive_count", sa.Integer(), nullable=False),
        sa.Column("report_sha256", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("skill_candidate_evaluations")
    op.drop_index("ix_skill_evaluation_runs_proposal_id", table_name="skill_evaluation_runs")
    with op.batch_alter_table("skill_evaluation_runs") as batch:
        batch.drop_constraint("fk_skill_evaluation_runs_proposal_id", type_="foreignkey")
        batch.drop_column("proposal_id")
        batch.alter_column("revision_id", existing_type=sa.String(), nullable=False)
