"""Add schedule delivery authority and per-fire snapshots."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schedule_delivery_policies",
        sa.Column("schedule_id", sa.String, sa.ForeignKey("schedules.id"), primary_key=True),
        sa.Column("route_id", sa.String, nullable=False),
        sa.Column("route_fingerprint", sa.String, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "schedule_fire_delivery_plans",
        sa.Column(
            "schedule_fire_id", sa.String, sa.ForeignKey("schedule_fires.id"), primary_key=True
        ),
        sa.Column("execution_id", sa.String, sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("route_id", sa.String, nullable=False),
        sa.Column("route_fingerprint", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index(
        "ix_schedule_fire_delivery_plans_execution_id",
        "schedule_fire_delivery_plans",
        ["execution_id"],
    )


def downgrade() -> None:
    op.drop_table("schedule_fire_delivery_plans")
    op.drop_table("schedule_delivery_policies")
