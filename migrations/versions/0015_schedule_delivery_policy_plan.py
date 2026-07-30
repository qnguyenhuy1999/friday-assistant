"""Scheduled delivery policy and per-fire authority snapshot; no historical backfill."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schedule_delivery_policies",
        sa.Column("schedule_id", sa.String(), sa.ForeignKey("schedules.id"), primary_key=True),
        sa.Column("route_id", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(route_id) BETWEEN 1 AND 64", name="ck_schedule_delivery_policies_route_id"
        ),
        sa.CheckConstraint("enabled IN (0, 1)", name="ck_schedule_delivery_policies_enabled"),
    )
    # SQLite requires the parent composite key to exist before the child FK.
    with op.batch_alter_table("schedule_fires") as batch:
        batch.create_unique_constraint("uq_schedule_fires_binding", ["id", "schedule_id", "run_id"])
    op.create_table(
        "schedule_fire_delivery_plans",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("schedule_fire_id", sa.String(), nullable=False),
        sa.Column("schedule_id", sa.String(), nullable=False),
        sa.Column("execution_id", sa.String(), nullable=False),
        sa.Column("route_id", sa.String(64), nullable=False),
        sa.Column("route_fingerprint", sa.String(64)),
        sa.Column("content_source", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("reason_code", sa.String(128)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("schedule_fire_id", name="uq_schedule_fire_delivery_plans_fire"),
        sa.ForeignKeyConstraint(
            ["schedule_fire_id", "schedule_id", "execution_id"],
            ["schedule_fires.id", "schedule_fires.schedule_id", "schedule_fires.run_id"],
            name="fk_schedule_fire_delivery_plans_fire_binding",
        ),
        sa.CheckConstraint(
            "status IN ('ready', 'suppressed')", name="ck_schedule_fire_delivery_plans_status"
        ),
        sa.CheckConstraint(
            "content_source = 'final_agent_summary_v1'",
            name="ck_schedule_fire_delivery_plans_content",
        ),
        sa.CheckConstraint(
            "route_fingerprint IS NULL OR (length(route_fingerprint) = 64 AND "
            "route_fingerprint NOT GLOB '*[^0-9a-f]*')",
            name="ck_schedule_fire_delivery_plans_fingerprint",
        ),
        sa.CheckConstraint(
            "(status = 'ready' AND route_fingerprint IS NOT NULL AND reason_code IS NULL) OR "  # noqa: E501
            "(status = 'suppressed' AND route_fingerprint IS NULL AND reason_code IN "
            "('schedule_delivery_route_missing', 'schedule_delivery_route_disabled'))",
            name="ck_schedule_fire_delivery_plans_shape",
        ),
        sa.CheckConstraint(
            "length(route_id) BETWEEN 1 AND 64", name="ck_schedule_fire_delivery_plans_route_id"
        ),
    )


def downgrade() -> None:
    op.drop_table("schedule_fire_delivery_plans")
    with op.batch_alter_table("schedule_fires") as batch:
        batch.drop_constraint("uq_schedule_fires_binding", type_="unique")
    op.drop_table("schedule_delivery_policies")
