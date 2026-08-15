"""Add durable Workflow execution snapshots and DAG scheduler state."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


_WORKFLOW_EXECUTION_STATE_SHAPE = (
    "(status = 'running' AND completed_at IS NULL AND failure_code IS NULL "
    "AND failure_message IS NULL) OR "
    "(status = 'succeeded' AND completed_at IS NOT NULL AND failure_code IS NULL "
    "AND failure_message IS NULL) OR "
    "(status = 'failed' AND completed_at IS NOT NULL AND failure_code IS NOT NULL "
    "AND failure_message IS NOT NULL) OR "
    "(status = 'cancelled' AND completed_at IS NOT NULL AND failure_code IS NULL)"
)

_WORKFLOW_NODE_EXECUTION_STATE_SHAPE = (
    "(status = 'pending' AND child_task_id IS NULL AND child_run_id IS NULL "
    "AND child_execution_id IS NULL AND started_at IS NULL AND completed_at IS NULL "
    "AND failure_code IS NULL AND failure_message IS NULL) OR "
    "(status = 'dispatched' AND child_task_id IS NOT NULL AND child_run_id IS NOT NULL "
    "AND child_execution_id IS NOT NULL AND started_at IS NOT NULL AND completed_at IS NULL "
    "AND failure_code IS NULL AND failure_message IS NULL) OR "
    "(status = 'succeeded' AND child_task_id IS NOT NULL AND child_run_id IS NOT NULL "
    "AND child_execution_id IS NOT NULL AND started_at IS NOT NULL "
    "AND completed_at IS NOT NULL AND failure_code IS NULL AND failure_message IS NULL) OR "
    "(status = 'failed' AND child_task_id IS NOT NULL AND child_run_id IS NOT NULL "
    "AND child_execution_id IS NOT NULL AND started_at IS NOT NULL "
    "AND completed_at IS NOT NULL AND failure_code IS NOT NULL "
    "AND failure_message IS NOT NULL) OR "
    "(status = 'cancelled' AND completed_at IS NOT NULL AND failure_code IS NULL "
    "AND failure_message IS NULL AND "
    "((child_task_id IS NULL AND child_run_id IS NULL AND child_execution_id IS NULL "
    "AND started_at IS NULL) OR "
    "(child_task_id IS NOT NULL AND child_run_id IS NOT NULL "
    "AND child_execution_id IS NOT NULL AND started_at IS NOT NULL))) OR "
    "(status = 'blocked' AND completed_at IS NOT NULL AND child_task_id IS NULL "
    "AND child_run_id IS NULL AND child_execution_id IS NULL "
    "AND failure_code IS NOT NULL AND failure_message IS NOT NULL)"
)


def upgrade() -> None:
    # 0034 owns the immutable Workflow registry.
    # Step-4 owns the Run marker and state changes.
    with op.batch_alter_table("runs") as batch:
        batch.add_column(
            sa.Column(
                "workflow_execution_id",
                sa.String(),
                nullable=True,
            )
        )
        batch.create_index(
            "ix_runs_workflow_execution_id",
            ["workflow_execution_id"],
        )
        batch.drop_constraint("ck_runs_status", type_="check")
        batch.drop_constraint("ck_runs_wait_marker_shape", type_="check")
        batch.create_check_constraint(
            "ck_runs_status",
            "status IN ("
            "'queued', 'running', "
            "'waiting_for_approval', "
            "'waiting_for_delegation', "
            "'waiting_for_workflow', "
            "'succeeded', 'failed', 'cancelled')",
        )
        batch.create_check_constraint(
            "ck_runs_wait_marker_shape",
            "((status = 'waiting_for_approval' AND "
            "approval_request_id IS NOT NULL AND "
            "delegation_request_id IS NULL AND "
            "workflow_execution_id IS NULL) OR "
            "(status = 'waiting_for_delegation' AND "
            "delegation_request_id IS NOT NULL AND "
            "approval_request_id IS NULL AND "
            "workflow_execution_id IS NULL) OR "
            "(status = 'waiting_for_workflow' AND "
            "workflow_execution_id IS NOT NULL AND "
            "approval_request_id IS NULL AND "
            "delegation_request_id IS NULL) OR "
            "(status NOT IN ('waiting_for_approval', "
            "'waiting_for_delegation', 'waiting_for_workflow') AND "
            "approval_request_id IS NULL AND "
            "delegation_request_id IS NULL AND "
            "(workflow_execution_id IS NULL OR status IN "
            "('succeeded', 'failed', 'cancelled'))))",
        )
    op.create_table(
        "workflow_executions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("root_run_id", sa.String(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("workflow_id", sa.String(), sa.ForeignKey("workflows.id"), nullable=False),
        sa.Column("workflow_revision_id", sa.String(), nullable=False),
        sa.Column("workflow_content_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("failure_code", sa.String(), nullable=True),
        sa.Column("failure_message", sa.String(), nullable=True),
        sa.UniqueConstraint("root_run_id", name="uq_workflow_executions_root_run_id"),
        sa.UniqueConstraint("id", "root_run_id", name="uq_workflow_executions_id_root_run"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled')",
            name="ck_workflow_executions_status",
        ),
        # Candidate key targeted by WorkflowNodeExecution's composite
        # frozen-revision ownership FK.
        sa.UniqueConstraint(
            "id", "workflow_revision_id", name="uq_workflow_executions_id_revision"
        ),
        sa.CheckConstraint(
            "length(workflow_content_sha256) = 64 AND "
            "workflow_content_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_workflow_executions_sha256",
        ),
        sa.CheckConstraint(
            _WORKFLOW_EXECUTION_STATE_SHAPE,
            name="ck_workflow_executions_state_shape",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "workflow_revision_id"],
            ["workflow_revisions.workflow_id", "workflow_revisions.id"],
            name="fk_workflow_executions_revision_ownership",
        ),
    )
    op.create_index("ix_workflow_executions_root_run_id", "workflow_executions", ["root_run_id"])

    # This reverse edge also prevents a Run from claiming an execution whose
    # root_run_id names a different Run.  It is added after the target table
    # exists; 0034 deliberately only introduced the nullable marker.
    with op.batch_alter_table("runs") as batch:
        batch.create_foreign_key(
            "fk_runs_workflow_execution_root_ownership",
            "workflow_executions",
            ["workflow_execution_id", "id"],
            ["id", "root_run_id"],
        )

    op.create_table(
        "workflow_node_executions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "workflow_execution_id",
            sa.String(),
            sa.ForeignKey("workflow_executions.id"),
            nullable=False,
        ),
        sa.Column(
            "workflow_node_id", sa.String(), sa.ForeignKey("workflow_nodes.id"), nullable=False
        ),
        sa.Column("workflow_revision_id", sa.String(), nullable=False),
        sa.Column("node_key", sa.String(128), nullable=False),
        sa.Column("target_agent_id", sa.String(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("target_agent_revision_id", sa.String(), nullable=False),
        sa.Column("target_agent_revision_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("child_task_id", sa.String(), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("child_run_id", sa.String(), sa.ForeignKey("runs.id"), nullable=True),
        sa.Column("child_execution_id", sa.String(), nullable=True),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("failure_code", sa.String(), nullable=True),
        sa.Column("failure_message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "workflow_execution_id",
            "workflow_node_id",
            name="uq_workflow_node_executions_execution_node",
        ),
        sa.UniqueConstraint("child_task_id", name="uq_workflow_node_executions_child_task_id"),
        sa.UniqueConstraint("child_run_id", name="uq_workflow_node_executions_child_run_id"),
        sa.CheckConstraint(
            "status IN ('pending', 'dispatched', 'succeeded', 'failed', 'cancelled', 'blocked')",
            name="ck_workflow_node_executions_status",
        ),
        sa.CheckConstraint(
            "length(target_agent_revision_sha256) = 64 AND "
            "target_agent_revision_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_workflow_node_executions_sha256",
        ),
        sa.CheckConstraint(
            _WORKFLOW_NODE_EXECUTION_STATE_SHAPE,
            name="ck_workflow_node_executions_state_shape",
        ),
        sa.ForeignKeyConstraint(
            ["target_agent_id", "target_agent_revision_id"],
            ["agent_revisions.agent_id", "agent_revisions.id"],
            name="fk_workflow_node_executions_revision_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["child_task_id", "target_agent_id"],
            ["task_agent_bindings.task_id", "task_agent_bindings.agent_id"],
            name="fk_workflow_node_executions_child_agent_ownership",
        ),
        # Structural ownership proof: the node must belong to the exact frozen
        # revision recorded by this execution, and that revision must be the
        # one the owning WorkflowExecution froze.
        sa.ForeignKeyConstraint(
            ["workflow_revision_id", "workflow_node_id"],
            ["workflow_nodes.revision_id", "workflow_nodes.id"],
            name="fk_workflow_node_executions_node_revision_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_execution_id", "workflow_revision_id"],
            ["workflow_executions.id", "workflow_executions.workflow_revision_id"],
            name="fk_workflow_node_executions_execution_revision_ownership",
        ),
    )
    op.create_index(
        "ix_workflow_node_executions_workflow_execution_id",
        "workflow_node_executions",
        ["workflow_execution_id"],
    )
    op.create_index(
        "ix_workflow_node_executions_child_execution_id",
        "workflow_node_executions",
        ["child_execution_id"],
    )

    op.create_table(
        "run_workflow_resolutions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("workflow_id", sa.String(), sa.ForeignKey("workflows.id"), nullable=False),
        sa.Column("workflow_revision_id", sa.String(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_run_workflow_resolutions_run_id"),
        sa.CheckConstraint(
            "length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_run_workflow_resolutions_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "workflow_revision_id"],
            ["workflow_revisions.workflow_id", "workflow_revisions.id"],
            name="fk_run_workflow_resolutions_revision_ownership",
        ),
    )

    op.create_table(
        "task_workflow_bindings",
        sa.Column("task_id", sa.String(), sa.ForeignKey("tasks.id"), primary_key=True),
        sa.Column("workflow_id", sa.String(), sa.ForeignKey("workflows.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def _reject_downgrade_if_state_exists() -> None:
    bind = op.get_bind()
    checks = (
        ("workflow_executions", "SELECT count(*) FROM workflow_executions"),
        ("workflow_node_executions", "SELECT count(*) FROM workflow_node_executions"),
        ("run_workflow_resolutions", "SELECT count(*) FROM run_workflow_resolutions"),
        ("task_workflow_bindings", "SELECT count(*) FROM task_workflow_bindings"),
        (
            "runs",
            "SELECT count(*) FROM runs WHERE workflow_execution_id IS NOT NULL "
            "OR status = 'waiting_for_workflow'",
        ),
    )
    for table, statement in checks:
        if bind.scalar(sa.text(statement)):
            raise RuntimeError(f"0035 cannot downgrade while {table} contains durable state")


def downgrade() -> None:
    _reject_downgrade_if_state_exists()

    with op.batch_alter_table("runs") as batch:
        batch.drop_constraint("fk_runs_workflow_execution_root_ownership", type_="foreignkey")

    op.drop_index(
        "ix_workflow_node_executions_child_execution_id",
        table_name="workflow_node_executions",
    )
    op.drop_index(
        "ix_workflow_node_executions_workflow_execution_id",
        table_name="workflow_node_executions",
    )
    op.drop_table("workflow_node_executions")
    op.drop_table("run_workflow_resolutions")
    op.drop_table("task_workflow_bindings")
    op.drop_index("ix_workflow_executions_root_run_id", table_name="workflow_executions")
    op.drop_table("workflow_executions")
    with op.batch_alter_table("runs") as batch:
        batch.drop_constraint("ck_runs_wait_marker_shape", type_="check")
        batch.drop_constraint("ck_runs_status", type_="check")
        batch.create_check_constraint(
            "ck_runs_status",
            "status IN ("
            "'queued', 'running', "
            "'waiting_for_approval', "
            "'waiting_for_delegation', "
            "'succeeded', 'failed', 'cancelled')",
        )
        batch.create_check_constraint(
            "ck_runs_wait_marker_shape",
            "((status = 'waiting_for_approval' AND "
            "approval_request_id IS NOT NULL AND "
            "delegation_request_id IS NULL) OR "
            "(status = 'waiting_for_delegation' AND "
            "delegation_request_id IS NOT NULL AND "
            "approval_request_id IS NULL) OR "
            "(status NOT IN ('waiting_for_approval', "
            "'waiting_for_delegation') AND "
            "approval_request_id IS NULL AND "
            "delegation_request_id IS NULL))",
        )
        batch.drop_index("ix_runs_workflow_execution_id")
        batch.drop_column("workflow_execution_id")
