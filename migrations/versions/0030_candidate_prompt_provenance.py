"""Persist candidate-prompt provenance so it can be reproduced and verified.

The candidate generator now builds one canonical, deterministic prompt from
persisted inputs only (base revision, evidence snapshot, code-owned
configuration). This revision adds the two columns needed to prove that
later: the prompt-builder version and the sha256 of the exact prompt bytes
sent to the brain adapter for each proposal.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

# 0029's SQLite triggers reference skill_improvement_proposals by name.
# SQLite's batch-alter rebuild (copy, drop, rename) breaks any trigger still
# pointing at the old table, so these must be dropped before the rebuild and
# restored from their captured DDL afterward.
_SQLITE_TRIGGERS = (
    "ck_skill_active_pointer_authority",
    "ck_consumed_skill_approval_success",
    "ck_promoted_revision_success",
    "ck_generated_revision_canonical_approval",
)


def upgrade() -> None:
    bind = op.get_bind()
    trigger_sql: dict[str, str] = {}
    if bind.dialect.name == "sqlite":
        for trigger in _SQLITE_TRIGGERS:
            trigger_sql[trigger] = bind.execute(
                sa.text("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = :name"),
                {"name": trigger},
            ).scalar_one()
            bind.exec_driver_sql(f"DROP TRIGGER {trigger}")
    with op.batch_alter_table("skill_improvement_proposals") as batch:
        batch.add_column(sa.Column("candidate_prompt_version", sa.String(), nullable=False))
        batch.add_column(sa.Column("candidate_prompt_sha256", sa.String(), nullable=False))
        batch.create_check_constraint(
            "ck_skill_improvement_proposals_candidate_prompt_hash",
            "length(candidate_prompt_sha256) = 64 AND "
            "candidate_prompt_sha256 NOT GLOB '*[^0-9a-f]*'",
        )
    for sql in trigger_sql.values():
        bind.exec_driver_sql(sql)


def downgrade() -> None:
    bind = op.get_bind()
    trigger_sql: dict[str, str] = {}
    if bind.dialect.name == "sqlite":
        for trigger in _SQLITE_TRIGGERS:
            trigger_sql[trigger] = bind.execute(
                sa.text("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = :name"),
                {"name": trigger},
            ).scalar_one()
            bind.exec_driver_sql(f"DROP TRIGGER {trigger}")
    with op.batch_alter_table("skill_improvement_proposals") as batch:
        batch.drop_constraint("ck_skill_improvement_proposals_candidate_prompt_hash", type_="check")
        batch.drop_column("candidate_prompt_sha256")
        batch.drop_column("candidate_prompt_version")
    for sql in trigger_sql.values():
        bind.exec_driver_sql(sql)
