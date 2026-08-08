"""Persist bounded immutable evidence selections for skill improvements."""

# ruff: noqa: E501

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_evidence_snapshots",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("skill_id", sa.String(), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column(
            "base_revision_id", sa.String(), sa.ForeignKey("skill_revisions.id"), nullable=False
        ),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("content_sha256", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("skill_evidence_snapshots")
