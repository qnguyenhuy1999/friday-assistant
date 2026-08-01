"""Allow approved improvement proposals to become generated revisions."""

from __future__ import annotations

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("skill_revisions") as batch_op:
        batch_op.drop_constraint("ck_skill_revisions_source_kind", type_="check")
        batch_op.create_check_constraint(
            "ck_skill_revisions_source_kind",
            "source_kind IN ('operator', 'imported', 'generated')",
        )


def downgrade() -> None:
    with op.batch_alter_table("skill_revisions") as batch_op:
        batch_op.drop_constraint("ck_skill_revisions_source_kind", type_="check")
        batch_op.create_check_constraint(
            "ck_skill_revisions_source_kind", "source_kind IN ('operator', 'imported')"
        )
