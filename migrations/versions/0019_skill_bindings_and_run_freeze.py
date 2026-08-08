"""Bind operator skills to tasks and freeze exact revisions per run."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_skill_bindings",
        sa.Column("task_id", sa.String(), sa.ForeignKey("tasks.id"), primary_key=True),
        sa.Column("skill_id", sa.String(), sa.ForeignKey("skills.id"), primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("task_id", "skill_id", name="uq_task_skill_bindings_skill"),
        sa.UniqueConstraint("task_id", "position", name="uq_task_skill_bindings_position"),
        sa.CheckConstraint("position BETWEEN 1 AND 16", name="ck_task_skill_bindings_position"),
    )
    op.create_table(
        "run_skill_resolutions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("runs.id"), nullable=False, unique=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "run_skill_bindings",
        sa.Column("run_id", sa.String(), sa.ForeignKey("runs.id"), primary_key=True),
        sa.Column("skill_id", sa.String(), nullable=False, primary_key=True),
        sa.Column("revision_id", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint("run_id", "skill_id", name="uq_run_skill_bindings_skill"),
        sa.UniqueConstraint("run_id", "position", name="uq_run_skill_bindings_position"),
        sa.ForeignKeyConstraint(
            ["skill_id", "revision_id"],
            ["skill_revisions.skill_id", "skill_revisions.id"],
            name="fk_run_skill_bindings_revision_ownership",
        ),
        sa.CheckConstraint("position BETWEEN 1 AND 16", name="ck_run_skill_bindings_position"),
    )


def downgrade() -> None:
    op.drop_table("run_skill_bindings")
    op.drop_table("run_skill_resolutions")
    op.drop_table("task_skill_bindings")
