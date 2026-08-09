"""Activate durable parent/child delegation execution state.

The downgrade is intentionally preflighted before any batch DDL. Pure
Step-1 REQUESTED rows remain compatible with 0032; dispatched or terminal
execution provenance is never silently discarded.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


_DELEGATION_STATE_SHAPE = (
    "(status = 'requested' AND child_task_id IS NULL AND child_run_id IS NULL "
    "AND started_at IS NULL AND completed_at IS NULL AND failure_code IS NULL) OR "
    "(status = 'dispatched' AND child_task_id IS NOT NULL AND child_run_id IS NOT NULL "
    "AND started_at IS NOT NULL AND completed_at IS NULL AND failure_code IS NULL) OR "
    "(status = 'succeeded' AND child_task_id IS NOT NULL AND child_run_id IS NOT NULL "
    "AND started_at IS NOT NULL AND completed_at IS NOT NULL AND failure_code IS NULL) OR "
    "(status = 'failed' AND child_task_id IS NOT NULL AND child_run_id IS NOT NULL "
    "AND started_at IS NOT NULL AND completed_at IS NOT NULL AND failure_code IS NOT NULL) OR "
    "(status = 'cancelled' AND failure_code IS NULL AND "
    "((child_task_id IS NULL AND child_run_id IS NULL AND started_at IS NULL) OR "
    "(child_task_id IS NOT NULL AND child_run_id IS NOT NULL AND started_at IS NOT NULL)) "
    "AND completed_at IS NOT NULL)"
)


def upgrade() -> None:
    with op.batch_alter_table("delegation_requests") as batch:
        batch.create_unique_constraint("uq_delegation_requests_id_parent", ["id", "parent_run_id"])
        batch.create_unique_constraint("uq_delegation_requests_child_task_id", ["child_task_id"])
        batch.create_unique_constraint("uq_delegation_requests_child_run_id", ["child_run_id"])
        batch.create_check_constraint("ck_delegation_requests_state_shape", _DELEGATION_STATE_SHAPE)

    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("delegation_request_id", sa.String(), nullable=True))
        batch.create_index("ix_runs_delegation_request_id", ["delegation_request_id"])
        batch.drop_constraint("ck_runs_status", type_="check")
        batch.create_check_constraint(
            "ck_runs_status",
            "status IN ('queued', 'running', 'waiting_for_approval', 'waiting_for_delegation', "
            "'succeeded', 'failed', 'cancelled')",
        )
        batch.create_check_constraint(
            "ck_runs_wait_marker_shape",
            "((status = 'waiting_for_approval' AND approval_request_id IS NOT NULL "
            "AND delegation_request_id IS NULL) OR "
            "(status = 'waiting_for_delegation' AND delegation_request_id IS NOT NULL "
            "AND approval_request_id IS NULL) OR "
            "(status NOT IN ('waiting_for_approval', 'waiting_for_delegation') "
            "AND approval_request_id IS NULL AND delegation_request_id IS NULL))",
        )
        batch.create_foreign_key(
            "fk_runs_delegation_parent_ownership",
            "delegation_requests",
            ["delegation_request_id", "id"],
            ["id", "parent_run_id"],
        )


def _reject_downgrade_if_state_exists() -> None:
    bind = op.get_bind()
    run_state = bind.scalar(
        sa.text(
            "SELECT count(*) FROM runs "
            "WHERE status = 'waiting_for_delegation' OR delegation_request_id IS NOT NULL"
        )
    )
    delegation_state = bind.scalar(
        sa.text(
            "SELECT count(*) FROM delegation_requests "
            "WHERE status <> 'requested' OR child_task_id IS NOT NULL "
            "OR child_run_id IS NOT NULL OR started_at IS NOT NULL "
            "OR completed_at IS NOT NULL OR failure_code IS NOT NULL"
        )
    )
    if run_state or delegation_state:
        raise RuntimeError("0033 cannot downgrade while Step-2 delegation state exists")


def downgrade() -> None:
    _reject_downgrade_if_state_exists()

    with op.batch_alter_table("runs") as batch:
        batch.drop_constraint("fk_runs_delegation_parent_ownership", type_="foreignkey")
        batch.drop_constraint("ck_runs_wait_marker_shape", type_="check")
        batch.drop_constraint("ck_runs_status", type_="check")
        batch.create_check_constraint(
            "ck_runs_status",
            "status IN ('queued', 'running', 'waiting_for_approval', 'succeeded', 'failed', "
            "'cancelled')",
        )
        batch.drop_index("ix_runs_delegation_request_id")
        batch.drop_column("delegation_request_id")

    with op.batch_alter_table("delegation_requests") as batch:
        batch.drop_constraint("ck_delegation_requests_state_shape", type_="check")
        batch.drop_constraint("uq_delegation_requests_child_run_id", type_="unique")
        batch.drop_constraint("uq_delegation_requests_child_task_id", type_="unique")
        batch.drop_constraint("uq_delegation_requests_id_parent", type_="unique")
