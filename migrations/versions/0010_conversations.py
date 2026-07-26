"""Add the conversation interaction layer."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "conversation_turns",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("conversation_id", sa.String, sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("client_turn_id", sa.String, nullable=False),
        sa.Column("input_text", sa.String, nullable=False),
        sa.Column("input_mode", sa.String, nullable=False),
        sa.Column("recognition_language", sa.String),
        sa.Column("task_id", sa.String, sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("run_id", sa.String, sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint(
            "conversation_id", "client_turn_id", name="uq_conversation_turns_client_turn_id"
        ),
        sa.UniqueConstraint("run_id", name="uq_conversation_turns_run_id"),
        sa.CheckConstraint(
            "input_mode IN ('typed', 'push_to_talk', 'hands_free')",
            name="ck_conversation_turns_input_mode",
        ),
    )
    op.create_index(
        "ix_conversation_turns_conversation_id", "conversation_turns", ["conversation_id"]
    )
    op.create_index(
        "ix_conversation_turns_conversation_id_created_at_id",
        "conversation_turns",
        ["conversation_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_table("conversation_turns")
    op.drop_table("conversations")
