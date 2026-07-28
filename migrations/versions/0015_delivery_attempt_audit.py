"""Persist claim-fenced outbound delivery attempts."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "delivery_attempts",
        sa.Column(
            "delivery_id", sa.String(), sa.ForeignKey("outbound_deliveries.id"), primary_key=True
        ),
        sa.Column("claim_generation", sa.Integer(), primary_key=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("claim_owner", sa.String(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("failure_code", sa.String(), nullable=True),
        sa.CheckConstraint("attempt_number > 0", name="ck_delivery_attempts_attempt_number"),
        sa.CheckConstraint("claim_generation > 0", name="ck_delivery_attempts_claim_generation"),
        sa.CheckConstraint(
            "outcome IN ('delivered', 'failed', 'ambiguous', 'queued') OR outcome IS NULL",
            name="ck_delivery_attempts_outcome",
        ),
    )
    op.create_index("ix_delivery_attempts_delivery_id", "delivery_attempts", ["delivery_id"])


def downgrade() -> None:
    op.drop_index("ix_delivery_attempts_delivery_id", table_name="delivery_attempts")
    op.drop_table("delivery_attempts")
