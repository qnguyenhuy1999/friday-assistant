"""Add generic external-target provenance to tool invocations."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_COLUMNS = (
    "provenance_kind",
    "provenance_target",
    "provenance_remote_name",
    "provenance_binding_fingerprint",
)


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column("tool_invocations", sa.Column(name, sa.String, nullable=True))


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column("tool_invocations", name)
