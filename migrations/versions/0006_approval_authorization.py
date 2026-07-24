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
    op.add_column("approval_requests", sa.Column("authorization_fingerprint", sa.String()))
    op.add_column("approval_requests", sa.Column("consumed_at", sa.DateTime()))


def downgrade() -> None:
    op.drop_column("approval_requests", "consumed_at")
    op.drop_column("approval_requests", "authorization_fingerprint")
