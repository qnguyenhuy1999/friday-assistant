"""Add durable, secret-free outbound delivery attempt ledger.

The table is not enough on its own. A database that already ran Step 4 can
contain deliveries whose `dispatch_started_at` is set but which have no ledger
row, because the ledger did not exist when they were dispatched. From Step 5
on, post-dispatch recovery fails closed when the matching attempt is absent, so
such a delivery would be permanently unrecoverable.

This revision therefore backfills one attempt per already-dispatched delivery,
reconstructed only from timestamps the old schema already recorded truthfully:

    SENDING    -> in_progress, finished_at NULL, no failure code
    DELIVERED  -> delivered,   finished_at = delivered_at or updated_at
    FAILED     -> failed,      finished_at = updated_at, existing failure code
    AMBIGUOUS  -> ambiguous,   finished_at = updated_at, existing failure code

Pre-dispatch rows (`dispatch_started_at IS NULL`) get no attempt: no boundary
was crossed, so there is nothing to audit and inventing a row would be a lie.

Where the existing data cannot produce truthful audit history — a crossed
boundary with no claim generation, a status the boundary cannot precede, a
terminal timestamp before the boundary, or a failure code that is not a stable
lowercase code — the migration raises instead of writing something invalid.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

#: A stable lowercase failure code: 1..128 chars of [a-z0-9_]. GLOB is
#: case-sensitive in SQLite (LIKE is not), so the negated class also rejects
#: uppercase and whitespace.
_FAILURE_CODE_SQL = (
    "length(failure_code) BETWEEN 1 AND 128 AND failure_code NOT GLOB '*[^a-z0-9_]*'"
)

#: Statuses a delivery can legitimately hold once the boundary is crossed.
#: QUEUED is excluded because a requeue requires an unmarked boundary, and
#: CANCELLED because cancellation is only legal from QUEUED.
_POST_DISPATCH_STATUSES = ("sending", "delivered", "failed", "ambiguous")

#: Guards on pre-existing data. Each selects the rows that make truthful audit
#: history impossible; any match aborts the migration.
_IMPOSSIBLE_SHAPES: tuple[tuple[str, str], ...] = (
    (
        "claim_generation <= 0",
        "a crossed dispatch boundary with no claim generation",
    ),
    (
        "status NOT IN ('sending', 'delivered', 'failed', 'ambiguous')",
        "a status the dispatch boundary cannot precede",
    ),
    (
        "status IN ('failed', 'ambiguous') "
        f"AND (failure_code IS NULL OR NOT ({_FAILURE_CODE_SQL}))",
        "a terminal failure without a stable lowercase failure code",
    ),
    (
        "status = 'delivered' AND COALESCE(delivered_at, updated_at) < dispatch_started_at",
        "a delivery timestamp before the dispatch boundary",
    ),
    (
        "status IN ('failed', 'ambiguous') AND updated_at < dispatch_started_at",
        "a terminal timestamp before the dispatch boundary",
    ),
)


def _reject_impossible_shapes(connection: sa.Connection) -> None:
    for predicate, description in _IMPOSSIBLE_SHAPES:
        offending = connection.execute(
            sa.text(
                "SELECT id FROM outbound_deliveries "
                f"WHERE dispatch_started_at IS NOT NULL AND ({predicate}) LIMIT 5"
            )
        ).scalars()
        ids = list(offending)
        if ids:
            raise RuntimeError(
                "0014 cannot backfill delivery_attempts: "
                f"{len(ids)} or more outbound deliveries have {description}. "
                f"Offending delivery ids: {', '.join(ids)}"
            )


def _backfill_dispatched_attempts(connection: sa.Connection) -> None:
    """Insert one attempt per already-dispatched delivery.

    Timestamps are moved as the raw stored strings so the backfilled rows are
    byte-identical in format to rows the application writes, and canonical
    UUIDs are generated here because SQLite has no UUID function.
    """
    rows = connection.execute(
        sa.text(
            """
            SELECT id,
                   claim_generation,
                   status,
                   dispatch_started_at,
                   CASE status
                       WHEN 'sending'   THEN NULL
                       WHEN 'delivered' THEN COALESCE(delivered_at, updated_at)
                       ELSE updated_at
                   END AS finished_at,
                   CASE WHEN status IN ('failed', 'ambiguous') THEN failure_code END AS failure_code
            FROM outbound_deliveries
            WHERE dispatch_started_at IS NOT NULL
              AND status IN :statuses
            ORDER BY id
            """
        ).bindparams(sa.bindparam("statuses", expanding=True)),
        {"statuses": list(_POST_DISPATCH_STATUSES)},
    ).all()
    if not rows:
        return
    connection.execute(
        sa.text(
            "INSERT INTO delivery_attempts "
            "(id, delivery_id, claim_generation, started_at, finished_at, outcome, failure_code) "
            "VALUES (:id, :delivery_id, :claim_generation, :started_at, :finished_at, "
            ":outcome, :failure_code)"
        ),
        [
            {
                "id": str(uuid.uuid4()),
                "delivery_id": row.id,
                "claim_generation": row.claim_generation,
                "started_at": row.dispatch_started_at,
                "finished_at": row.finished_at,
                # SENDING is still in flight; every other post-dispatch status
                # maps onto the identically-named terminal attempt outcome.
                "outcome": "in_progress" if row.status == "sending" else row.status,
                "failure_code": row.failure_code,
            }
            for row in rows
        ],
    )


def upgrade() -> None:
    op.create_table(
        "delivery_attempts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "delivery_id", sa.String(), sa.ForeignKey("outbound_deliveries.id"), nullable=False
        ),
        sa.Column("claim_generation", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("failure_code", sa.String(), nullable=True),
        sa.UniqueConstraint(
            "delivery_id", "claim_generation", name="uq_delivery_attempts_delivery_generation"
        ),
        sa.CheckConstraint("claim_generation > 0", name="ck_delivery_attempts_claim_generation"),
        sa.CheckConstraint(
            "outcome IN ('in_progress', 'delivered', 'failed', 'ambiguous')",
            name="ck_delivery_attempts_outcome",
        ),
        sa.CheckConstraint(
            "(outcome = 'in_progress' AND finished_at IS NULL AND failure_code IS NULL) OR "
            "(outcome = 'delivered' AND finished_at IS NOT NULL AND failure_code IS NULL) OR "
            "(outcome IN ('failed', 'ambiguous') AND finished_at IS NOT NULL "
            "AND failure_code IS NOT NULL)",
            name="ck_delivery_attempts_lifecycle",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_delivery_attempts_finished_after_started",
        ),
        sa.CheckConstraint(
            f"failure_code IS NULL OR ({_FAILURE_CODE_SQL})",
            name="ck_delivery_attempts_failure_code_shape",
        ),
    )
    op.create_index(
        "ix_delivery_attempts_delivery_started", "delivery_attempts", ["delivery_id", "started_at"]
    )
    connection = op.get_bind()
    _reject_impossible_shapes(connection)
    _backfill_dispatched_attempts(connection)


def downgrade() -> None:
    op.drop_index("ix_delivery_attempts_delivery_started", table_name="delivery_attempts")
    op.drop_table("delivery_attempts")
