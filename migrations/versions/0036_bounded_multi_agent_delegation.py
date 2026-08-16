"""Durable bounded nested-delegation lineage.

Adds the minimum durable lineage to delegation_requests for Step 5A:

* ``root_delegation_id`` — the delegation tree identity.  A root delegation
  is its own root; every nested delegation names its incoming delegation's
  root.
* ``depth`` — 1-based hop count under the repository convention
  (A -> B = 1, B -> C = 2, ...).

The incoming parent delegation itself stays derived from the canonical
execution lineage (``child_run_id -> runs.execution_id``) instead of a
duplicate ownership column.

Historical compatibility: nested delegation never existed before 0036, so
every pre-existing row is a true root delegation at depth 1.  The backfill
records exactly that fact and fabricates nothing else.

The downgrade refuses while any nested (depth > 1) row exists so Step-5A
lineage is never silently discarded.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("delegation_requests") as batch:
        batch.add_column(sa.Column("root_delegation_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("depth", sa.Integer(), nullable=True))

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE delegation_requests SET root_delegation_id = id, depth = 1 "
            "WHERE root_delegation_id IS NULL OR depth IS NULL"
        )
    )

    with op.batch_alter_table("delegation_requests") as batch:
        batch.alter_column("root_delegation_id", existing_type=sa.String(), nullable=False)
        batch.alter_column("depth", existing_type=sa.Integer(), nullable=False)
        batch.create_check_constraint("ck_delegation_requests_depth_positive", "depth >= 1")
        batch.create_check_constraint(
            "ck_delegation_requests_root_shape",
            "(depth = 1 AND root_delegation_id = id) OR (depth > 1 AND root_delegation_id <> id)",
        )
        batch.create_foreign_key(
            "fk_delegation_requests_root_ownership",
            "delegation_requests",
            ["root_delegation_id"],
            ["id"],
        )
    op.create_index(
        "ix_delegation_requests_root_delegation_id",
        "delegation_requests",
        ["root_delegation_id"],
    )


def _reject_downgrade_if_nested_state_exists() -> None:
    bind = op.get_bind()
    nested = bind.scalar(sa.text("SELECT count(*) FROM delegation_requests WHERE depth > 1"))
    if nested:
        raise RuntimeError("0036 cannot downgrade while nested delegation lineage exists")


def downgrade() -> None:
    _reject_downgrade_if_nested_state_exists()

    op.drop_index("ix_delegation_requests_root_delegation_id", table_name="delegation_requests")
    with op.batch_alter_table("delegation_requests") as batch:
        batch.drop_constraint("fk_delegation_requests_root_ownership", type_="foreignkey")
        batch.drop_constraint("ck_delegation_requests_root_shape", type_="check")
        batch.drop_constraint("ck_delegation_requests_depth_positive", type_="check")
        batch.drop_column("depth")
        batch.drop_column("root_delegation_id")
