"""Approval authorization binding: exact-action fingerprint + one-shot consumption.

Both columns are nullable — every pre-Phase-11 approval simply has no
fingerprint (it can never authorize a fingerprint-bound tool action) and no
consumption timestamp. No backfill is needed or possible: the fingerprint is
derived from a tool call that never existed for historical approvals."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("approval_requests") as batch:
        batch.add_column(sa.Column("authorization_fingerprint", sa.String(64)))
        batch.add_column(sa.Column("consumed_at", sa.DateTime()))
        batch.create_check_constraint(
            "ck_approval_authorization_fingerprint_hex",
            "authorization_fingerprint IS NULL OR (length(authorization_fingerprint) = 64 "
            "AND authorization_fingerprint NOT GLOB '*[^0-9a-f]*')",
        )


def downgrade() -> None:
    with op.batch_alter_table("approval_requests") as batch:
        batch.drop_constraint("ck_approval_authorization_fingerprint_hex", type_="check")
        batch.drop_column("consumed_at")
        batch.drop_column("authorization_fingerprint")
