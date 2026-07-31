"""Freeze route body bounds and record run-scoped content rejections."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Historical plans predate the body-bound snapshot.  Leave them NULL: Step
    # 7 must fail closed instead of inferring authority from current config.
    with op.batch_alter_table("schedule_fire_delivery_plans") as batch:
        batch.add_column(sa.Column("route_max_body_chars", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("content_rejected_run_id", sa.String(), nullable=True))
        batch.create_check_constraint(
            "ck_schedule_fire_delivery_plans_route_max_body_chars",
            "route_max_body_chars IS NULL OR route_max_body_chars > 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("schedule_fire_delivery_plans") as batch:
        batch.drop_constraint("ck_schedule_fire_delivery_plans_route_max_body_chars", type_="check")
        batch.drop_column("content_rejected_run_id")
        batch.drop_column("route_max_body_chars")
