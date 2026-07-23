"""Add the optional approval expiry deadline column."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("approval_requests", sa.Column("expires_at", sa.DateTime, nullable=True))


def downgrade() -> None:
    op.drop_column("approval_requests", "expires_at")
