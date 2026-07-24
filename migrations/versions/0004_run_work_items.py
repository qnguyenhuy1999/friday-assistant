"""Add durable run work items."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_work_items",
        sa.Column("run_id", sa.String, sa.ForeignKey("runs.id"), primary_key=True),
        sa.Column("available_at", sa.DateTime, nullable=False),
        sa.Column("enqueued_at", sa.DateTime, nullable=False),
        sa.Column("claimed_by", sa.String),
        sa.Column("claim_token", sa.String),
        sa.Column("claim_generation", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("claimed_at", sa.DateTime),
        sa.Column("heartbeat_at", sa.DateTime),
        sa.Column("lease_expires_at", sa.DateTime),
    )
    op.create_index("ix_run_work_items_available_at", "run_work_items", ["available_at"])
    op.create_index("ix_run_work_items_lease_expires_at", "run_work_items", ["lease_expires_at"])
    op.create_index(
        "ix_run_work_items_available_at_enqueued_at_run_id",
        "run_work_items",
        ["available_at", "enqueued_at", "run_id"],
    )
    op.execute(
        """
        INSERT INTO run_work_items (
            run_id,
            available_at,
            enqueued_at,
            claimed_by,
            claim_token,
            claim_generation,
            claimed_at,
            heartbeat_at,
            lease_expires_at
        )
        SELECT
            id,
            created_at,
            created_at,
            NULL,
            NULL,
            0,
            NULL,
            NULL,
            NULL
        FROM runs
        WHERE status IN ('queued', 'running')
        """
    )


def downgrade() -> None:
    op.drop_table("run_work_items")
