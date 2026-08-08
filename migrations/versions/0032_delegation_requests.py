"""Add the delegation_requests provenance edge from a parent Run to a future
child Task/Run. Step 1 is persistence only: no orchestration, no execution.

run_steps has no prior UNIQUE(run_id, id) — every other table with a
step_id FK (approvals, tool_invocations, artifacts) only points at
run_steps.id and never enforces the step's run_id matches its own run_id at
the database level. delegation_requests is the first to need that fence, so
this migration adds the UNIQUE(run_id, id) composite FK target on run_steps
before creating the table.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("run_steps") as batch:
        batch.create_unique_constraint("uq_run_steps_run_id_id", ["run_id", "id"])
    op.create_table(
        "delegation_requests",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("parent_run_id", sa.String(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("parent_run_step_id", sa.String(), nullable=True),
        sa.Column("target_agent_id", sa.String(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("objective", sa.String(), nullable=False),
        sa.Column("input_payload", sa.JSON(), nullable=False),
        sa.Column("expected_output_contract", sa.String(), nullable=False),
        sa.Column("authorization_fingerprint", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("child_task_id", sa.String(), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("child_run_id", sa.String(), sa.ForeignKey("runs.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("failure_code", sa.String(), nullable=True),
        sa.CheckConstraint(
            "status IN ('requested', 'dispatched', 'succeeded', 'failed', 'cancelled')",
            name="ck_delegation_requests_status",
        ),
        sa.CheckConstraint(
            "length(authorization_fingerprint) = 64 AND "
            "authorization_fingerprint NOT GLOB '*[^0-9a-f]*'",
            name="ck_delegation_requests_fingerprint",
        ),
        sa.CheckConstraint(
            "(status = 'failed' AND failure_code IS NOT NULL) OR "
            "(status != 'failed' AND failure_code IS NULL)",
            name="ck_delegation_requests_failure_shape",
        ),
        sa.CheckConstraint(
            "length(objective) BETWEEN 1 AND 4000", name="ck_delegation_requests_objective_length"
        ),
        sa.CheckConstraint(
            "length(expected_output_contract) BETWEEN 1 AND 4000",
            name="ck_delegation_requests_output_contract_length",
        ),
        sa.UniqueConstraint("authorization_fingerprint", name="uq_delegation_requests_fingerprint"),
        sa.ForeignKeyConstraint(
            ["parent_run_id", "parent_run_step_id"],
            ["run_steps.run_id", "run_steps.id"],
            name="fk_delegation_requests_step_ownership",
        ),
    )
    op.create_index(
        "ix_delegation_requests_parent_run_id", "delegation_requests", ["parent_run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_delegation_requests_parent_run_id", table_name="delegation_requests")
    op.drop_table("delegation_requests")
    with op.batch_alter_table("run_steps") as batch:
        batch.drop_constraint("uq_run_steps_run_id_id", type_="unique")
