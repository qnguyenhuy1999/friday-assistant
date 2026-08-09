"""Add durable, Friday-owned Agent registry: agents, agent_revisions,
task_agent_bindings, run_agent_resolutions.

Unlike the original skills/skill_revisions pair (0017), which needed a
follow-up migration (0018) to add the active-revision ownership fence
retroactively, this is a brand-new entity pair — the composite ownership FK
is created here from the start.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("key", sa.String(), nullable=False, unique=True),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("active_revision_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('active', 'disabled', 'archived')", name="ck_agents_status"),
    )
    op.create_table(
        "agent_revisions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("agent_id", sa.String(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("instructions", sa.String(), nullable=False),
        sa.Column("runtime_kind", sa.String(), nullable=False),
        sa.Column("runtime_config", sa.JSON(), nullable=False),
        sa.Column("content_sha256", sa.String(), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_revisions_agent_version"),
        sa.UniqueConstraint("agent_id", "id", name="uq_agent_revisions_agent_id"),
        sa.CheckConstraint("version > 0", name="ck_agent_revisions_version"),
        sa.CheckConstraint(
            "length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_agent_revisions_sha256",
        ),
        sa.CheckConstraint(
            "source_kind IN ('operator', 'imported')", name="ck_agent_revisions_source_kind"
        ),
    )
    op.create_index("ix_agent_revisions_agent_id", "agent_revisions", ["agent_id"])
    with op.batch_alter_table("agents") as batch:
        batch.create_foreign_key(
            "fk_agents_active_revision_ownership",
            "agent_revisions",
            ["id", "active_revision_id"],
            ["agent_id", "id"],
        )
    op.create_table(
        "task_agent_bindings",
        sa.Column("task_id", sa.String(), sa.ForeignKey("tasks.id"), primary_key=True),
        sa.Column("agent_id", sa.String(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "run_agent_resolutions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("runs.id"), nullable=False, unique=True),
        sa.Column("agent_id", sa.String(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("revision_id", sa.String(), sa.ForeignKey("agent_revisions.id"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id", "revision_id"],
            ["agent_revisions.agent_id", "agent_revisions.id"],
            name="fk_run_agent_resolutions_revision_ownership",
        ),
    )


def downgrade() -> None:
    # These tables are Phase-21 durable state.  Dropping them would silently
    # destroy identity, revision, binding, or frozen-resolution provenance.
    # Keep every check before the first DDL so a rejected downgrade is atomic.
    bind = op.get_bind()
    for table in (
        "agents",
        "agent_revisions",
        "task_agent_bindings",
        "run_agent_resolutions",
    ):
        if bind.scalar(sa.text(f"SELECT count(*) FROM {table}")):
            raise RuntimeError(f"0031 cannot downgrade while {table} contains durable state")

    op.drop_table("run_agent_resolutions")
    op.drop_table("task_agent_bindings")
    with op.batch_alter_table("agents") as batch:
        batch.drop_constraint("fk_agents_active_revision_ownership", type_="foreignkey")
    op.drop_index("ix_agent_revisions_agent_id", table_name="agent_revisions")
    op.drop_table("agent_revisions")
    op.drop_table("agents")
