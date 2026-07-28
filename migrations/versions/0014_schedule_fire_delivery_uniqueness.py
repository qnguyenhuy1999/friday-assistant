"""Fence scheduled answer delivery to one intent per ScheduleFire."""

from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_outbound_deliveries_source_schedule_fire_id",
        "outbound_deliveries",
        ["source_schedule_fire_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_outbound_deliveries_source_schedule_fire_id", "outbound_deliveries")
