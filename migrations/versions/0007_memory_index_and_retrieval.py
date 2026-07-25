"""Add memory index snapshots and retrieval audit tables.

Records only what answers "which notes influenced this Run, which index
snapshot was used, which lines were retrieved, was the excerpt truncated" —
never excerpt bodies, absolute paths, or query text (see
.herdr/phase12-invariants.md, "Hard security rules")."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_index_snapshots",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("vault_identity_hash", sa.String, nullable=False),
        sa.Column("source_snapshot_hash", sa.String, nullable=False),
        sa.Column("graph_checksum", sa.String),
        sa.Column("graphify_version", sa.String),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("built_at", sa.DateTime, nullable=False),
        sa.Column("file_count", sa.Integer, nullable=False),
        sa.Column("node_count", sa.Integer, nullable=False),
        sa.Column("edge_count", sa.Integer, nullable=False),
        sa.Column("failure_code", sa.String),
    )
    op.create_table(
        "memory_retrieval_records",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("run_id", sa.String, sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("turn_number", sa.Integer, nullable=False),
        sa.Column("query_hash", sa.String, nullable=False),
        sa.Column("source_snapshot_id", sa.String),
        sa.Column("index_snapshot_id", sa.String, sa.ForeignKey("memory_index_snapshots.id")),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("candidate_count", sa.Integer, nullable=False),
        sa.Column("selected_count", sa.Integer, nullable=False),
    )
    op.create_index("ix_memory_retrieval_records_run_id", "memory_retrieval_records", ["run_id"])
    op.create_table(
        "memory_retrieval_items",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column(
            "record_id",
            sa.String,
            sa.ForeignKey("memory_retrieval_records.id"),
            nullable=False,
        ),
        sa.Column("path", sa.String, nullable=False),
        sa.Column("heading", sa.String),
        sa.Column("start_line", sa.Integer, nullable=False),
        sa.Column("end_line", sa.Integer, nullable=False),
        sa.Column("content_hash", sa.String, nullable=False),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("methods", sa.JSON, nullable=False),
        sa.Column("truncated", sa.Boolean, nullable=False),
    )
    op.create_index("ix_memory_retrieval_items_record_id", "memory_retrieval_items", ["record_id"])


def downgrade() -> None:
    op.drop_table("memory_retrieval_items")
    op.drop_table("memory_retrieval_records")
    op.drop_table("memory_index_snapshots")
