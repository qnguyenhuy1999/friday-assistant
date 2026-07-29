"""Add the durable outbound-delivery dispatch boundary marker.

`dispatch_started_at` is written immediately before a future dispatcher
crosses the external side-effect boundary. Recovery reads it to decide
whether an expired SENDING lease may be safely requeued (NULL: nothing was
sent) or must become AMBIGUOUS (NOT NULL: a send may have happened).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "outbound_deliveries",
        sa.Column("dispatch_started_at", sa.DateTime, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("outbound_deliveries", "dispatch_started_at")
