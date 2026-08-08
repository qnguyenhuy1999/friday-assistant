"""Link every skill improvement proposal to its durable evidence package."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("skill_improvement_proposals") as batch_op:
        batch_op.add_column(sa.Column("evidence_snapshot_id", sa.String(), nullable=True))
        batch_op.create_foreign_key(
            "fk_skill_improvement_proposals_evidence_snapshot_id",
            "skill_evidence_snapshots",
            ["evidence_snapshot_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("skill_improvement_proposals") as batch_op:
        batch_op.drop_constraint(
            "fk_skill_improvement_proposals_evidence_snapshot_id", type_="foreignkey"
        )
        batch_op.drop_column("evidence_snapshot_id")
