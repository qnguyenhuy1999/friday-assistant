"""Persist exact approval-bound Skill promotion and rollback requests."""

# ruff: noqa: E501

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_promotion_requests",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "proposal_id",
            sa.String(),
            sa.ForeignKey("skill_improvement_proposals.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("skill_id", sa.String(), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column(
            "base_revision_id", sa.String(), sa.ForeignKey("skill_revisions.id"), nullable=False
        ),
        sa.Column(
            "expected_active_revision_id",
            sa.String(),
            sa.ForeignKey("skill_revisions.id"),
            nullable=False,
        ),
        sa.Column("candidate_sha256", sa.String(), nullable=False),
        sa.Column(
            "candidate_evaluation_id",
            sa.String(),
            sa.ForeignKey("skill_candidate_evaluations.id"),
            nullable=False,
        ),
        sa.Column("comparison_report_sha256", sa.String(), nullable=False),
        sa.Column("target_version", sa.Integer(), nullable=False),
        sa.Column("authorization_fingerprint", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime()),
        sa.Column("resolver", sa.String()),
        sa.Column("promoted_revision_id", sa.String(), sa.ForeignKey("skill_revisions.id")),
        sa.CheckConstraint("target_version > 0", name="ck_skill_promotion_target_version"),
    )
    op.create_table(
        "skill_rollback_requests",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("skill_id", sa.String(), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column(
            "expected_current_revision_id",
            sa.String(),
            sa.ForeignKey("skill_revisions.id"),
            nullable=False,
        ),
        sa.Column(
            "target_revision_id", sa.String(), sa.ForeignKey("skill_revisions.id"), nullable=False
        ),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("authorization_fingerprint", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime()),
        sa.Column("resolver", sa.String()),
    )


def downgrade() -> None:
    op.drop_table("skill_rollback_requests")
    op.drop_table("skill_promotion_requests")
