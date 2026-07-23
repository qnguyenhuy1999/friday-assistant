from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("description", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("started_at", sa.DateTime),
        sa.Column("completed_at", sa.DateTime),
        sa.Column("failed_at", sa.DateTime),
        sa.Column("cancelled_at", sa.DateTime),
        sa.Column("failure", sa.JSON),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("task_id", sa.String, sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("started_at", sa.DateTime),
        sa.Column("ended_at", sa.DateTime),
        sa.Column("failure", sa.JSON),
        sa.Column("approval_request_id", sa.String, index=True),
    )
    op.create_index("ix_runs_task_id", "runs", ["task_id"])
    op.create_table(
        "run_steps",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("run_id", sa.String, sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("started_at", sa.DateTime),
        sa.Column("ended_at", sa.DateTime),
        sa.Column("failure", sa.JSON),
        sa.Column("approval_request_id", sa.String, index=True),
        sa.UniqueConstraint("run_id", "position", name="uq_run_steps_run_id_position"),
    )
    op.create_index("ix_run_steps_run_id", "run_steps", ["run_id"])
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("run_id", sa.String, sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("step_id", sa.String, sa.ForeignKey("run_steps.id")),
        sa.Column("category", sa.String, nullable=False),
        sa.Column("summary", sa.String, nullable=False),
        sa.Column("reason", sa.String, nullable=False),
        sa.Column("requested_action", sa.String, nullable=False),
        sa.Column("requested_input", sa.JSON),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("requested_at", sa.DateTime, nullable=False),
        sa.Column("resolved_at", sa.DateTime),
        sa.Column("resolution_note", sa.String),
        sa.Column("resolver", sa.String),
    )
    op.create_index("ix_approval_requests_run_id", "approval_requests", ["run_id"])
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("run_id", sa.String, sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("step_id", sa.String, sa.ForeignKey("run_steps.id")),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("media_type", sa.String, nullable=False),
        sa.Column("location", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("size", sa.Integer),
        sa.Column("checksum", sa.String),
        sa.Column("metadata", sa.JSON),
    )
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"])
    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("run_id", sa.String, sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("step_id", sa.String, sa.ForeignKey("run_steps.id")),
        sa.Column("approval_request_id", sa.String, index=True),
        sa.Column("tool_name", sa.String, nullable=False),
        sa.Column("requested_input", sa.JSON),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("requested_at", sa.DateTime, nullable=False),
        sa.Column("started_at", sa.DateTime),
        sa.Column("completed_at", sa.DateTime),
        sa.Column("output", sa.JSON),
        sa.Column("output_set", sa.Boolean, nullable=False),
        sa.Column("failure", sa.JSON),
    )
    op.create_index("ix_tool_invocations_run_id", "tool_invocations", ["run_id"])
    op.create_table(
        "run_events",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("run_id", sa.String, sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("step_id", sa.String, sa.ForeignKey("run_steps.id")),
        sa.Column("type", sa.String, nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("occurred_at", sa.DateTime, nullable=False),
        sa.Column("payload", sa.JSON),
        sa.UniqueConstraint("run_id", "sequence", name="uq_run_events_run_id_sequence"),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])


def downgrade() -> None:
    op.drop_table("run_events")
    op.drop_table("tool_invocations")
    op.drop_table("artifacts")
    op.drop_table("approval_requests")
    op.drop_table("run_steps")
    op.drop_table("runs")
    op.drop_table("tasks")
