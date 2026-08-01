"""Persist one durable safe-improvement policy per Skill."""

# ruff: noqa: E501

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_improvement_policies",
        sa.Column("skill_id", sa.String(), sa.ForeignKey("skills.id"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("minimum_usage_records", sa.Integer(), nullable=False),
        sa.Column("minimum_failures", sa.Integer(), nullable=False),
        sa.Column("minimum_harmful_feedback", sa.Integer(), nullable=False),
        sa.Column(
            "evaluation_suite_id",
            sa.String(),
            sa.ForeignKey("skill_evaluation_suites.id"),
            nullable=False,
        ),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False),
        sa.Column("max_open_proposals", sa.Integer(), nullable=False),
        sa.Column("evidence_window_size", sa.Integer(), nullable=False),
        sa.Column("generator_version", sa.String(), nullable=False),
        sa.Column("comparison_policy_version", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_triggered_at", sa.DateTime()),
    )


def downgrade() -> None:
    op.drop_table("skill_improvement_policies")
