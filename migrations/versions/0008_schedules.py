"""Add durable schedules and idempotent schedule fires."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("task_id", sa.String, sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("cron", sa.String),
        sa.Column("run_at", sa.DateTime),
        sa.Column("timezone", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("next_fire_at", sa.DateTime),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'completed', 'cancelled')",
            name="ck_schedules_status",
        ),
    )
    op.create_index("ix_schedules_task_id", "schedules", ["task_id"])
    op.create_index("ix_schedules_due", "schedules", ["status", "next_fire_at"])
    op.create_table(
        "schedule_fires",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("schedule_id", sa.String, sa.ForeignKey("schedules.id"), nullable=False),
        sa.Column("scheduled_for", sa.DateTime, nullable=False),
        sa.Column("fired_at", sa.DateTime, nullable=False),
        sa.Column("run_id", sa.String, sa.ForeignKey("runs.id"), nullable=False),
        sa.UniqueConstraint("schedule_id", "scheduled_for", name="uq_schedule_fires_occurrence"),
        sa.UniqueConstraint("run_id", name="uq_schedule_fires_run_id"),
    )
    op.create_index("ix_schedule_fires_schedule_id", "schedule_fires", ["schedule_id"])


def downgrade() -> None:
    op.drop_table("schedule_fires")
    op.drop_table("schedules")
