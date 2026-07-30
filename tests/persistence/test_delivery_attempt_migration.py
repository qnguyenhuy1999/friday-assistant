"""0014 must reconstruct truthful audit history for Step-4 databases.

A database upgraded from Step 4 can already contain deliveries whose dispatch
boundary was crossed before the ledger existed. From Step 5 on, post-dispatch
recovery fails closed when the matching attempt is missing, so those rows would
be permanently unrecoverable. These tests upgrade to 0013, write representative
Step-4 data by hand, then upgrade to 0014 and prove the backfill is both
complete and honest.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from friday.application.delivery_lifecycle import (
    LEASE_EXPIRED_AFTER_DISPATCH,
    RecoverExpiredDeliveryClaims,
)
from friday.application.retry_policy import RetryPolicy
from friday.domain import DeliveryAttemptOutcome, DeliveryId, DeliveryStatus
from friday.infrastructure.persistence.database import create_session_factory
from friday.infrastructure.persistence.repositories import (
    DeliveryAttemptRepository,
    OutboundDeliveryRepository,
)
from friday.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork

REPO_ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 1, 1, tzinfo=UTC)
LEASE = timedelta(minutes=1)
FINGERPRINT = "a" * 64
BODY = "hello"
#: Must be the real digest: the domain aggregate re-verifies it on read, so a
#: placeholder would make every backfilled row unloadable.
BODY_SHA = hashlib.sha256(BODY.encode("utf-8")).hexdigest()
RETRY_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=timedelta(seconds=30),
    multiplier=2.0,
    max_delay=timedelta(minutes=5),
)


def _stamp(value: datetime) -> str:
    """Render a timestamp exactly the way SQLAlchemy stores DateTime on SQLite."""
    return value.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")


def _alembic_config(db_path: Path) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


@dataclass(frozen=True, slots=True)
class Step4Row:
    """One hand-written pre-0014 outbound delivery."""

    label: str
    status: str
    dispatch_started_at: datetime | None
    claim_generation: int
    updated_at: datetime
    delivered_at: datetime | None = None
    failure_code: str | None = None
    claim_owner: str | None = None
    claim_token: str | None = None
    claim_expires_at: datetime | None = None
    delivery_id: str = ""

    def with_id(self) -> Step4Row:
        from dataclasses import replace

        return replace(self, delivery_id=str(uuid.uuid4()))


BOUNDARY = T0 + timedelta(seconds=5)
FINISHED = T0 + timedelta(seconds=9)

#: One representative row per Step-4 shape the backfill must handle.
STEP4_ROWS: tuple[Step4Row, ...] = (
    Step4Row(
        label="sending_in_flight",
        status="sending",
        dispatch_started_at=BOUNDARY,
        claim_generation=1,
        updated_at=BOUNDARY,
        claim_owner="worker-a",
        claim_token="token-a",
        claim_expires_at=T0 + LEASE,
    ),
    Step4Row(
        label="delivered_with_delivered_at",
        status="delivered",
        dispatch_started_at=BOUNDARY,
        claim_generation=2,
        updated_at=FINISHED,
        delivered_at=FINISHED,
    ),
    Step4Row(
        label="delivered_without_delivered_at",
        status="delivered",
        dispatch_started_at=BOUNDARY,
        claim_generation=3,
        updated_at=FINISHED,
        delivered_at=None,
    ),
    Step4Row(
        label="failed",
        status="failed",
        dispatch_started_at=BOUNDARY,
        claim_generation=4,
        updated_at=FINISHED,
        failure_code="webhook_http_5xx",
    ),
    Step4Row(
        label="ambiguous",
        status="ambiguous",
        dispatch_started_at=BOUNDARY,
        claim_generation=5,
        updated_at=FINISHED,
        failure_code=LEASE_EXPIRED_AFTER_DISPATCH,
    ),
    # Pre-dispatch rows: no boundary crossed, so no audit history may exist.
    Step4Row(
        label="pre_dispatch_queued",
        status="queued",
        dispatch_started_at=None,
        claim_generation=0,
        updated_at=T0,
    ),
    Step4Row(
        label="pre_dispatch_sending",
        status="sending",
        dispatch_started_at=None,
        claim_generation=1,
        updated_at=T0,
        claim_owner="worker-b",
        claim_token="token-b",
        claim_expires_at=T0 + LEASE,
    ),
    Step4Row(
        label="pre_dispatch_failed",
        status="failed",
        dispatch_started_at=None,
        claim_generation=1,
        updated_at=T0,
        failure_code="delivery_pre_dispatch_attempts_exhausted",
    ),
    Step4Row(
        label="pre_dispatch_route_ambiguous",
        status="ambiguous",
        dispatch_started_at=None,
        claim_generation=1,
        updated_at=T0,
        failure_code="delivery_route_fingerprint_mismatch",
    ),
    Step4Row(
        label="pre_dispatch_cancelled",
        status="cancelled",
        dispatch_started_at=None,
        claim_generation=0,
        updated_at=T0,
    ),
)


def _insert_step4_delivery(connection: sa.Connection, row: Step4Row, *, run_id: str) -> None:
    invocation_id = str(uuid.uuid4())
    connection.execute(
        text(
            "INSERT INTO tool_invocations "
            "(id, run_id, tool_name, status, requested_at, output_set) "
            "VALUES (:id, :run_id, 'message.send', 'requested', :at, 0)"
        ),
        {"id": invocation_id, "run_id": run_id, "at": _stamp(T0)},
    )
    connection.execute(
        text(
            """
            INSERT INTO outbound_deliveries (
                id, source_kind, source_run_id, source_tool_invocation_id,
                source_schedule_fire_id, route_id, route_fingerprint, subject, body,
                body_sha256, status, available_at, attempt_count, claim_owner, claim_token,
                claim_generation, claim_expires_at, provider_message_id, failure_code,
                failure_message, created_at, updated_at, delivered_at, dispatch_started_at
            ) VALUES (
                :id, 'agent_request', :run_id, :invocation_id,
                NULL, 'personal.notifications', :fingerprint, NULL, :body,
                :body_sha256, :status, :available_at, 1, :claim_owner, :claim_token,
                :claim_generation, :claim_expires_at, NULL, :failure_code,
                NULL, :created_at, :updated_at, :delivered_at, :dispatch_started_at
            )
            """
        ),
        {
            "id": row.delivery_id,
            "run_id": run_id,
            "invocation_id": invocation_id,
            "fingerprint": FINGERPRINT,
            "body": BODY,
            "body_sha256": BODY_SHA,
            "status": row.status,
            "available_at": _stamp(T0),
            "claim_owner": row.claim_owner,
            "claim_token": row.claim_token,
            "claim_generation": row.claim_generation,
            "claim_expires_at": (
                _stamp(row.claim_expires_at) if row.claim_expires_at is not None else None
            ),
            "failure_code": row.failure_code,
            "created_at": _stamp(T0),
            "updated_at": _stamp(row.updated_at),
            "delivered_at": _stamp(row.delivered_at) if row.delivered_at is not None else None,
            "dispatch_started_at": (
                _stamp(row.dispatch_started_at) if row.dispatch_started_at is not None else None
            ),
        },
    )


def _seed_run(connection: sa.Connection) -> str:
    task_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
    connection.execute(
        text(
            "INSERT INTO tasks (id, title, description, status, created_at) "
            "VALUES (:id, 't', 'd', 'created', :at)"
        ),
        {"id": task_id, "at": _stamp(T0)},
    )
    connection.execute(
        text(
            "INSERT INTO runs (id, task_id, execution_id, status, created_at) "
            "VALUES (:id, :task_id, :id, 'created', :at)"
        ),
        {"id": run_id, "task_id": task_id, "at": _stamp(T0)},
    )
    return run_id


@dataclass(frozen=True, slots=True)
class MigratedFixture:
    """A 0014 database populated from hand-written Step-4 rows."""

    db_path: Path
    config: Config
    rows: dict[str, Step4Row]

    def attempts(self) -> dict[str, tuple[object, ...]]:
        """Map each labelled delivery to its backfilled attempt row, if any."""
        engine = sa.create_engine(f"sqlite:///{self.db_path}")
        try:
            with engine.connect() as connection:
                found = {
                    str(row.delivery_id): tuple(row)
                    for row in connection.execute(
                        text(
                            "SELECT delivery_id, claim_generation, started_at, finished_at, "
                            "outcome, failure_code, id FROM delivery_attempts"
                        )
                    )
                }
        finally:
            engine.dispose()
        return {
            label: found[row.delivery_id]
            for label, row in self.rows.items()
            if row.delivery_id in found
        }

    def attempt_count(self) -> int:
        engine = sa.create_engine(f"sqlite:///{self.db_path}")
        try:
            with engine.connect() as connection:
                return int(
                    connection.execute(text("SELECT COUNT(*) FROM delivery_attempts")).scalar_one()
                )
        finally:
            engine.dispose()


def _populate_0013(db_path: Path, rows: tuple[Step4Row, ...]) -> dict[str, Step4Row]:
    config = _alembic_config(db_path)
    command.upgrade(config, "0013")
    engine = sa.create_engine(f"sqlite:///{db_path}")
    identified = {row.label: row.with_id() for row in rows}
    try:
        with engine.begin() as connection:
            run_id = _seed_run(connection)
            for row in identified.values():
                _insert_step4_delivery(connection, row, run_id=run_id)
    finally:
        engine.dispose()
    return identified


@pytest.fixture
def migrated(tmp_path: Path) -> Iterator[MigratedFixture]:
    db_path = tmp_path / "backfill-0014.db"
    rows = _populate_0013(db_path, STEP4_ROWS)
    config = _alembic_config(db_path)
    command.upgrade(config, "0014")
    yield MigratedFixture(db_path=db_path, config=config, rows=rows)


# --------------------------------------------------------------------------
# Backfill correctness
# --------------------------------------------------------------------------


def test_backfill_creates_exactly_one_attempt_per_dispatched_delivery(
    migrated: MigratedFixture,
) -> None:
    dispatched = {
        label for label, row in migrated.rows.items() if row.dispatch_started_at is not None
    }

    assert set(migrated.attempts()) == dispatched
    assert migrated.attempt_count() == len(dispatched)


def test_backfilled_sending_row_is_in_progress(migrated: MigratedFixture) -> None:
    _, generation, started_at, finished_at, outcome, failure_code, attempt_id = migrated.attempts()[
        "sending_in_flight"
    ]

    assert outcome == DeliveryAttemptOutcome.IN_PROGRESS.value
    assert generation == 1  # > 0, as an actually-claimed delivery must be
    assert started_at == _stamp(BOUNDARY)
    assert finished_at is None
    assert failure_code is None
    assert uuid.UUID(str(attempt_id))  # canonical UUID


@pytest.mark.parametrize(
    ("label", "expected_finished_at"),
    [
        ("delivered_with_delivered_at", FINISHED),
        # No delivered_at recorded, so updated_at is the truthful terminal time.
        ("delivered_without_delivered_at", FINISHED),
    ],
)
def test_backfilled_delivered_row_uses_a_truthful_terminal_timestamp(
    migrated: MigratedFixture, label: str, expected_finished_at: datetime
) -> None:
    _, _, started_at, finished_at, outcome, failure_code, _ = migrated.attempts()[label]

    assert outcome == DeliveryAttemptOutcome.DELIVERED.value
    assert started_at == _stamp(BOUNDARY)
    assert finished_at == _stamp(expected_finished_at)
    assert failure_code is None


@pytest.mark.parametrize(
    ("label", "outcome", "failure_code"),
    [
        ("failed", DeliveryAttemptOutcome.FAILED.value, "webhook_http_5xx"),
        ("ambiguous", DeliveryAttemptOutcome.AMBIGUOUS.value, LEASE_EXPIRED_AFTER_DISPATCH),
    ],
)
def test_backfilled_coded_terminal_row_keeps_the_existing_failure_code(
    migrated: MigratedFixture, label: str, outcome: str, failure_code: str
) -> None:
    row = migrated.attempts()[label]

    assert row[4] == outcome
    assert row[2] == _stamp(BOUNDARY)
    assert row[3] == _stamp(FINISHED)
    assert row[5] == failure_code


@pytest.mark.parametrize(
    "label",
    [
        "pre_dispatch_queued",
        "pre_dispatch_sending",
        "pre_dispatch_failed",
        "pre_dispatch_route_ambiguous",
        "pre_dispatch_cancelled",
    ],
)
def test_no_attempt_is_invented_for_a_pre_dispatch_row(
    migrated: MigratedFixture, label: str
) -> None:
    """No boundary crossed means no audit history — inventing one would lie."""
    assert label not in migrated.attempts()


def test_backfilled_attempts_load_through_the_domain_mapper(
    migrated: MigratedFixture,
) -> None:
    """Every backfilled row must satisfy the domain invariants on read."""
    engine = sa.create_engine(f"sqlite:///{migrated.db_path}")
    session_factory = create_session_factory(engine)
    session = session_factory()
    try:
        repo = DeliveryAttemptRepository(session)
        for label, row in migrated.rows.items():
            if row.dispatch_started_at is None:
                continue
            loaded = repo.get_for_generation(
                DeliveryId.parse(row.delivery_id), row.claim_generation
            )
            assert loaded is not None, label
            assert loaded.started_at == BOUNDARY
            assert loaded.claim_generation == row.claim_generation
    finally:
        session.close()
        engine.dispose()


def test_a_backfilled_in_flight_row_can_still_recover_to_ambiguous(
    migrated: MigratedFixture,
) -> None:
    """The whole point: an in-flight Step-4 delivery is recoverable after 0014.

    Without the backfill this raises TransactionFailure, because post-dispatch
    recovery fails closed when the ledger row it must close does not exist.
    """
    dispatched = migrated.rows["sending_in_flight"]
    pre_dispatch = migrated.rows["pre_dispatch_sending"]
    engine = sa.create_engine(f"sqlite:///{migrated.db_path}")
    session_factory = create_session_factory(engine)

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory())

    recovered_at = T0 + LEASE + timedelta(seconds=1)
    try:
        with uow_factory() as uow:
            # Both expired SENDING rows recover, each down its own branch, and
            # neither raises TransactionFailure.
            assert (
                RecoverExpiredDeliveryClaims(RETRY_POLICY, candidate_limit=10).execute(
                    uow, recovered_at
                )
                == 2
            )
            uow.commit()

        session: Session = session_factory()
        try:
            deliveries = OutboundDeliveryRepository(session)
            attempts = DeliveryAttemptRepository(session)

            # Case B: the boundary was crossed, so the delivery parks AMBIGUOUS
            # and its backfilled attempt is the row that gets closed.
            after = deliveries.get(DeliveryId.parse(dispatched.delivery_id))
            assert after is not None
            assert after.status is DeliveryStatus.AMBIGUOUS
            assert after.failure_code == LEASE_EXPIRED_AFTER_DISPATCH
            attempt = attempts.get_for_generation(
                DeliveryId.parse(dispatched.delivery_id), dispatched.claim_generation
            )
            assert attempt is not None
            assert attempt.outcome is DeliveryAttemptOutcome.AMBIGUOUS
            assert attempt.failure_code == LEASE_EXPIRED_AFTER_DISPATCH
            assert attempt.finished_at == recovered_at
            assert attempt.started_at == BOUNDARY

            # Case A: no boundary, so it requeues and still audits nothing.
            requeued = deliveries.get(DeliveryId.parse(pre_dispatch.delivery_id))
            assert requeued is not None
            assert requeued.status is DeliveryStatus.QUEUED
            assert attempts.list_for_delivery(DeliveryId.parse(pre_dispatch.delivery_id), 10) == []
        finally:
            session.close()
    finally:
        engine.dispose()


def test_an_empty_step4_database_backfills_nothing(tmp_path: Path) -> None:
    db_path = tmp_path / "empty-0014.db"
    config = _alembic_config(db_path)
    command.upgrade(config, "0013")
    command.upgrade(config, "0014")

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT COUNT(*) FROM delivery_attempts")).scalar_one() == 0
            )
    finally:
        engine.dispose()


# --------------------------------------------------------------------------
# Impossible pre-existing shapes fail the migration
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "row", "match"),
    [
        (
            "zero_generation",
            Step4Row(
                label="zero_generation",
                status="sending",
                dispatch_started_at=BOUNDARY,
                claim_generation=0,
                updated_at=BOUNDARY,
            ),
            "no claim generation",
        ),
        (
            "queued_after_dispatch",
            Step4Row(
                label="queued_after_dispatch",
                status="queued",
                dispatch_started_at=BOUNDARY,
                claim_generation=1,
                updated_at=BOUNDARY,
            ),
            "status the dispatch boundary cannot precede",
        ),
        (
            "cancelled_after_dispatch",
            Step4Row(
                label="cancelled_after_dispatch",
                status="cancelled",
                dispatch_started_at=BOUNDARY,
                claim_generation=1,
                updated_at=BOUNDARY,
            ),
            "status the dispatch boundary cannot precede",
        ),
        (
            "failed_without_code",
            Step4Row(
                label="failed_without_code",
                status="failed",
                dispatch_started_at=BOUNDARY,
                claim_generation=1,
                updated_at=FINISHED,
                failure_code=None,
            ),
            "without a stable lowercase failure code",
        ),
        (
            "failed_with_free_form_code",
            Step4Row(
                label="failed_with_free_form_code",
                status="failed",
                dispatch_started_at=BOUNDARY,
                claim_generation=1,
                updated_at=FINISHED,
                failure_code="Connection refused to https://hook.test/secret",
            ),
            "without a stable lowercase failure code",
        ),
        (
            "delivered_before_boundary",
            Step4Row(
                label="delivered_before_boundary",
                status="delivered",
                dispatch_started_at=BOUNDARY,
                claim_generation=1,
                updated_at=T0,
                delivered_at=T0,
            ),
            "delivery timestamp before the dispatch boundary",
        ),
        (
            "failed_before_boundary",
            Step4Row(
                label="failed_before_boundary",
                status="failed",
                dispatch_started_at=BOUNDARY,
                claim_generation=1,
                updated_at=T0,
                failure_code="webhook_http_5xx",
            ),
            "terminal timestamp before the dispatch boundary",
        ),
    ],
)
def test_migration_fails_loudly_on_an_impossible_step4_shape(
    tmp_path: Path, label: str, row: Step4Row, match: str
) -> None:
    """Refuse to invent invalid audit history; make the operator look."""
    db_path = tmp_path / f"impossible-{label}.db"
    rows = _populate_0013(db_path, (row,))
    config = _alembic_config(db_path)

    with pytest.raises(RuntimeError, match=match) as excinfo:
        command.upgrade(config, "0014")

    # The message must name the offending row so it can actually be fixed.
    assert rows[label].delivery_id in str(excinfo.value)


# --------------------------------------------------------------------------
# Reversibility and structure
# --------------------------------------------------------------------------


def test_downgrade_to_0013_and_reupgrade_rebuilds_the_backfill(
    migrated: MigratedFixture,
) -> None:
    dispatched = len([row for row in migrated.rows.values() if row.dispatch_started_at is not None])
    assert migrated.attempt_count() == dispatched

    command.downgrade(migrated.config, "0013")
    engine = sa.create_engine(f"sqlite:///{migrated.db_path}")
    try:
        inspector = inspect(engine)
        assert "delivery_attempts" not in inspector.get_table_names()
        # The deliveries themselves, and their boundary markers, survive.
        assert "outbound_deliveries" in inspector.get_table_names()
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM outbound_deliveries "
                        "WHERE dispatch_started_at IS NOT NULL"
                    )
                ).scalar_one()
                == dispatched
            )
    finally:
        engine.dispose()

    command.upgrade(migrated.config, "0014")
    assert migrated.attempt_count() == dispatched
    assert set(migrated.attempts()) == {
        label for label, row in migrated.rows.items() if row.dispatch_started_at is not None
    }


def test_0014_creates_the_required_constraints_and_index(tmp_path: Path) -> None:
    db_path = tmp_path / "structure-0014.db"
    command.upgrade(_alembic_config(db_path), "0014")
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        inspector = inspect(engine)
        checks = {check["name"] for check in inspector.get_check_constraints("delivery_attempts")}
        assert {
            "ck_delivery_attempts_claim_generation",
            "ck_delivery_attempts_outcome",
            "ck_delivery_attempts_lifecycle",
            "ck_delivery_attempts_finished_after_started",
            "ck_delivery_attempts_failure_code_shape",
        } <= checks

        unique = {
            (constraint["name"], tuple(constraint["column_names"]))
            for constraint in inspector.get_unique_constraints("delivery_attempts")
        }
        assert (
            "uq_delivery_attempts_delivery_generation",
            ("delivery_id", "claim_generation"),
        ) in unique

        indexes = {
            (index["name"], tuple(index["column_names"]))
            for index in inspector.get_indexes("delivery_attempts")
        }
        assert (
            "ix_delivery_attempts_delivery_started",
            ("delivery_id", "started_at"),
        ) in indexes

        foreign_keys = {
            (tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
            for fk in inspector.get_foreign_keys("delivery_attempts")
        }
        assert (("delivery_id",), "outbound_deliveries", ("id",)) in foreign_keys
    finally:
        engine.dispose()
