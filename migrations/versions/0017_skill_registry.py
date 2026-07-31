"""Add durable immutable skill registry."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("key", sa.String(), nullable=False, unique=True),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("active_revision_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('active', 'disabled', 'archived')", name="ck_skills_status"),
    )
    op.create_table(
        "skill_revisions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("skill_id", sa.String(), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("instructions", sa.String(), nullable=False),
        sa.Column("content_sha256", sa.String(), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("skill_id", "version", name="uq_skill_revisions_skill_version"),
        sa.CheckConstraint("version > 0", name="ck_skill_revisions_version"),
        sa.CheckConstraint(
            "length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_skill_revisions_sha256",
        ),
        sa.CheckConstraint(
            "source_kind IN ('operator', 'imported')", name="ck_skill_revisions_source_kind"
        ),
    )
    op.create_index("ix_skill_revisions_skill_id", "skill_revisions", ["skill_id"])


def downgrade() -> None:
    op.drop_index("ix_skill_revisions_skill_id", table_name="skill_revisions")
    op.drop_table("skill_revisions")
    op.drop_table("skills")
