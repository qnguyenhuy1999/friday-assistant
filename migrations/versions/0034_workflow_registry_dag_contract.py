"""Create the immutable, definition-only Workflow registry and DAG tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("active_revision_id", sa.String()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("key", name="uq_workflows_key"),
        sa.CheckConstraint(
            "status IN ('active','disabled','archived')", name="ck_workflows_status"
        ),
    )
    op.create_table(
        "workflow_revisions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("workflow_id", sa.String(), sa.ForeignKey("workflows.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "workflow_id", "version", name="uq_workflow_revisions_workflow_version"
        ),
        sa.UniqueConstraint("workflow_id", "id", name="uq_workflow_revisions_workflow_id"),
        sa.CheckConstraint("version > 0", name="ck_workflow_revisions_version"),
        sa.CheckConstraint(
            "source_kind IN ('operator','imported')", name="ck_workflow_revisions_source_kind"
        ),
        sa.CheckConstraint(
            "length(content_sha256)=64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_workflow_revisions_sha256",
        ),
    )
    with op.batch_alter_table("workflows") as b:
        b.create_foreign_key(
            "fk_workflows_active_revision_ownership",
            "workflow_revisions",
            ["id", "active_revision_id"],
            ["workflow_id", "id"],
        )
    op.create_index("ix_workflow_revisions_workflow_id", "workflow_revisions", ["workflow_id"])
    op.create_table(
        "workflow_nodes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("revision_id", sa.String(), nullable=False),
        sa.Column("node_key", sa.String(128), nullable=False),
        sa.Column("target_agent_id", sa.String(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("objective", sa.String(), nullable=False),
        sa.Column("input_payload", sa.JSON(), nullable=False),
        sa.Column("expected_output_contract", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("revision_id", "id", name="uq_workflow_nodes_revision_id"),
        sa.UniqueConstraint("revision_id", "node_key", name="uq_workflow_nodes_revision_key"),
        sa.ForeignKeyConstraint(
            ["revision_id"], ["workflow_revisions.id"], name="fk_workflow_nodes_revision"
        ),
        sa.CheckConstraint("length(node_key) BETWEEN 1 AND 128", name="ck_workflow_nodes_key"),
        sa.CheckConstraint(
            "length(objective) BETWEEN 1 AND 4000", name="ck_workflow_nodes_objective"
        ),
        sa.CheckConstraint(
            "length(expected_output_contract) BETWEEN 1 AND 4000", name="ck_workflow_nodes_output"
        ),
    )
    op.create_index("ix_workflow_nodes_revision_id", "workflow_nodes", ["revision_id"])
    op.create_table(
        "workflow_edges",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("revision_id", sa.String(), nullable=False),
        sa.Column("from_node_id", sa.String(), nullable=False),
        sa.Column("to_node_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("revision_id", "id", name="uq_workflow_edges_revision_id"),
        sa.UniqueConstraint(
            "revision_id", "from_node_id", "to_node_id", name="uq_workflow_edges_pair"
        ),
        sa.ForeignKeyConstraint(
            ["revision_id", "from_node_id"],
            ["workflow_nodes.revision_id", "workflow_nodes.id"],
            name="fk_workflow_edges_from_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["revision_id", "to_node_id"],
            ["workflow_nodes.revision_id", "workflow_nodes.id"],
            name="fk_workflow_edges_to_ownership",
        ),
        sa.CheckConstraint("from_node_id <> to_node_id", name="ck_workflow_edges_not_self"),
    )
    op.create_index("ix_workflow_edges_revision_id", "workflow_edges", ["revision_id"])


def _reject() -> None:
    bind = op.get_bind()
    for table in ("workflows", "workflow_revisions", "workflow_nodes", "workflow_edges"):
        if bind.scalar(sa.text(f"SELECT count(*) FROM {table}")):
            raise RuntimeError("0034 cannot downgrade while Workflow state exists")


def downgrade() -> None:
    _reject()
    op.drop_index("ix_workflow_edges_revision_id", table_name="workflow_edges")
    op.drop_table("workflow_edges")
    op.drop_index("ix_workflow_nodes_revision_id", table_name="workflow_nodes")
    op.drop_table("workflow_nodes")
    with op.batch_alter_table("workflows") as b:
        b.drop_constraint("fk_workflows_active_revision_ownership", type_="foreignkey")
    op.drop_index("ix_workflow_revisions_workflow_id", table_name="workflow_revisions")
    op.drop_table("workflow_revisions")
    op.drop_table("workflows")
