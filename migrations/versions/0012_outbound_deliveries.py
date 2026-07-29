"""Add durable outbound delivery intents."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbound_deliveries",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("source_kind", sa.String, nullable=False),
        sa.Column("source_run_id", sa.String, sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("source_tool_invocation_id", sa.String, sa.ForeignKey("tool_invocations.id")),
        sa.Column("source_schedule_fire_id", sa.String, sa.ForeignKey("schedule_fires.id")),
        sa.Column("route_id", sa.String, nullable=False),
        sa.Column("route_fingerprint", sa.String, nullable=False),
        sa.Column("subject", sa.String),
        sa.Column("body", sa.String, nullable=False),
        sa.Column("body_sha256", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("available_at", sa.DateTime, nullable=False),
        sa.Column("attempt_count", sa.Integer, nullable=False),
        sa.Column("claim_owner", sa.String),
        sa.Column("claim_token", sa.String),
        sa.Column("claim_generation", sa.Integer, nullable=False),
        sa.Column("claim_expires_at", sa.DateTime),
        sa.Column("provider_message_id", sa.String),
        sa.Column("failure_code", sa.String),
        sa.Column("failure_message", sa.String),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.Column("delivered_at", sa.DateTime),
        sa.UniqueConstraint(
            "source_tool_invocation_id", name="uq_outbound_deliveries_source_tool_invocation_id"
        ),
        sa.UniqueConstraint(
            "source_schedule_fire_id", name="uq_outbound_deliveries_source_schedule_fire_id"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'sending', 'delivered', 'failed', 'ambiguous', 'cancelled')",
            name="ck_outbound_deliveries_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_outbound_deliveries_attempt_count"),
        sa.CheckConstraint("claim_generation >= 0", name="ck_outbound_deliveries_claim_generation"),
        sa.CheckConstraint(
            "(source_kind = 'agent_request' AND source_tool_invocation_id IS NOT NULL "
            "AND source_schedule_fire_id IS NULL) OR "
            "(source_kind = 'scheduled_run_answer' AND source_tool_invocation_id IS NULL "
            "AND source_schedule_fire_id IS NOT NULL)",
            name="ck_outbound_deliveries_source_shape",
        ),
        sa.CheckConstraint(
            "length(route_fingerprint) = 64 AND route_fingerprint NOT GLOB '*[^0-9a-f]*'",
            name="ck_outbound_deliveries_route_fingerprint_hex",
        ),
        sa.CheckConstraint(
            "length(body_sha256) = 64 AND body_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_outbound_deliveries_body_sha256_hex",
        ),
    )
    op.create_index(
        "ix_outbound_deliveries_due", "outbound_deliveries", ["status", "available_at", "id"]
    )


def downgrade() -> None:
    op.drop_table("outbound_deliveries")
