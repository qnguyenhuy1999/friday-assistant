"""Persist inert brain-only skill improvement candidates."""

# ruff: noqa: E501

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_improvement_proposals",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("skill_id", sa.String(), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column(
            "base_revision_id", sa.String(), sa.ForeignKey("skill_revisions.id"), nullable=False
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("trigger_kind", sa.String(), nullable=False),
        sa.Column("evidence_snapshot_hash", sa.String(), nullable=False),
        sa.Column("proposed_instructions", sa.String(), nullable=False),
        sa.Column("proposed_content_sha256", sa.String(), nullable=False),
        sa.Column("rationale", sa.String(), nullable=False),
        sa.Column("generator_version", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "skill_id",
            "base_revision_id",
            "evidence_snapshot_hash",
            "generator_version",
            name="uq_skill_improvement_proposal_fingerprint",
        ),
    )
    op.create_index(
        "ix_skill_improvement_proposals_skill_id", "skill_improvement_proposals", ["skill_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_skill_improvement_proposals_skill_id", table_name="skill_improvement_proposals"
    )
    op.drop_table("skill_improvement_proposals")
