"""Enforce active-revision ownership in the database.

skills.active_revision_id previously was a plain nullable string, so the
database accepted a pointer to a nonexistent revision or to a revision of
another skill. This adds the durable composite fence: skills(id,
active_revision_id) must reference skill_revisions(skill_id, id), which the
new UNIQUE(skill_id, id) on skill_revisions makes a valid FK target. NULL
active_revision_id (no activation yet) is exempt from the check.
"""

from __future__ import annotations

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The composite FK target must be unique before skills can reference it.
    with op.batch_alter_table("skill_revisions") as batch:
        batch.create_unique_constraint("uq_skill_revisions_skill_id", ["skill_id", "id"])
    with op.batch_alter_table("skills") as batch:
        batch.create_foreign_key(
            "fk_skills_active_revision_ownership",
            "skill_revisions",
            ["id", "active_revision_id"],
            ["skill_id", "id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("skills") as batch:
        batch.drop_constraint("fk_skills_active_revision_ownership", type_="foreignkey")
    with op.batch_alter_table("skill_revisions") as batch:
        batch.drop_constraint("uq_skill_revisions_skill_id", type_="unique")
