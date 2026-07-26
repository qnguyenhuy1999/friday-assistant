"""Add durable execution lineage for retry descendants."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("execution_id", sa.String(), nullable=True))
    op.execute("UPDATE runs SET execution_id = id WHERE execution_id IS NULL")
    with op.batch_alter_table("runs") as batch:
        batch.alter_column("execution_id", nullable=False)
        batch.create_index("ix_runs_execution_id", ["execution_id"])
    with op.batch_alter_table("schedules") as batch:
        batch.create_check_constraint("ck_schedules_kind", "kind IN ('once', 'cron')")
        batch.create_check_constraint(
            "ck_schedules_kind_shape",
            "(kind = 'once' AND run_at IS NOT NULL AND cron IS NULL) OR "
            "(kind = 'cron' AND cron IS NOT NULL AND run_at IS NULL)",
        )
        batch.create_check_constraint(
            "ck_schedules_status_next_fire",
            "(status = 'active' AND next_fire_at IS NOT NULL) OR (status = 'paused') OR "
            "(status IN ('completed', 'cancelled') AND next_fire_at IS NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("schedules") as batch:
        batch.drop_constraint("ck_schedules_status_next_fire", type_="check")
        batch.drop_constraint("ck_schedules_kind_shape", type_="check")
        batch.drop_constraint("ck_schedules_kind", type_="check")
    with op.batch_alter_table("runs") as batch:
        batch.drop_index("ix_runs_execution_id")
        batch.drop_column("execution_id")
