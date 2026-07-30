"""Add durable, secret-free outbound delivery attempt ledger."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "delivery_attempts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "delivery_id", sa.String(), sa.ForeignKey("outbound_deliveries.id"), nullable=False
        ),
        sa.Column("claim_generation", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("failure_code", sa.String(), nullable=True),
        sa.UniqueConstraint(
            "delivery_id", "claim_generation", name="uq_delivery_attempts_delivery_generation"
        ),
        sa.CheckConstraint("claim_generation > 0", name="ck_delivery_attempts_claim_generation"),
        sa.CheckConstraint(
            "outcome IN ('in_progress', 'delivered', 'failed', 'ambiguous')",
            name="ck_delivery_attempts_outcome",
        ),
        sa.CheckConstraint(
            "(outcome = 'in_progress' AND finished_at IS NULL) OR "
            "(outcome != 'in_progress' AND finished_at IS NOT NULL)",
            name="ck_delivery_attempts_lifecycle",
        ),
    )
    op.create_index(
        "ix_delivery_attempts_delivery_started", "delivery_attempts", ["delivery_id", "started_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_delivery_attempts_delivery_started", table_name="delivery_attempts")
    op.drop_table("delivery_attempts")
