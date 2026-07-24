"""Add atomic event sequence counters."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_event_sequence_counters",
        sa.Column("run_id", sa.String, sa.ForeignKey("runs.id"), primary_key=True),
        sa.Column("next_value", sa.Integer, nullable=False),
    )
    op.create_table(
        "task_event_sequence_counters",
        sa.Column("task_id", sa.String, sa.ForeignKey("tasks.id"), primary_key=True),
        sa.Column("next_value", sa.Integer, nullable=False),
    )
    op.execute(
        """
        INSERT INTO run_event_sequence_counters (run_id, next_value)
        SELECT run_id, MAX(sequence) + 1 FROM run_events GROUP BY run_id;
        """
    )
    op.execute(
        """
        INSERT INTO task_event_sequence_counters (task_id, next_value)
        SELECT task_id, MAX(sequence) + 1 FROM task_events GROUP BY task_id;
        """
    )


def downgrade() -> None:
    op.drop_table("task_event_sequence_counters")
    op.drop_table("run_event_sequence_counters")
