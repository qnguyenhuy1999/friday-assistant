"""Real-SQLite proofs for the durable delivery attempt ledger.

Every test here runs against the Alembic-migrated schema on disk and observes
results through a *separate* session, so an assertion can only pass if the
value is genuinely committed rather than sitting in one session's identity map.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from friday.application.delivery_lifecycle import (
    LEASE_EXPIRED_AFTER_DISPATCH,
    BeginDeliveryAttempt,
    ClaimNextDelivery,
    DeliveryClaim,
    PersistDeliveryOutcome,
    RecoverExpiredDeliveryClaims,
)
from friday.application.errors import ClaimLost
from friday.application.ports import MAX_DELIVERY_ATTEMPT_HISTORY_LIMIT
from friday.application.retry_policy import RetryPolicy
from friday.domain import (
    DeliveryAttemptId,
    DeliveryAttemptOutcome,
    DeliveryId,
    DeliverySourceKind,
    DeliveryStatus,
    OutboundDelivery,
    Run,
    RunId,
    Task,
    TaskId,
    ToolInvocation,
    ToolInvocationId,
)
from friday.domain.delivery_attempt import DeliveryAttempt
from friday.domain.errors import DomainValidationError
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.repositories import (
    DeliveryAttemptRepository,
    OutboundDeliveryRepository,
    RunRepository,
    TaskRepository,
    ToolInvocationRepository,
)
from friday.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork

T0 = datetime(2026, 1, 1, tzinfo=UTC)
FINGERPRINT = "a" * 64
LEASE = timedelta(minutes=1)
BOUNDARY = T0 + timedelta(seconds=5)
RETRY_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=timedelta(seconds=30),
    multiplier=2.0,
    max_delay=timedelta(minutes=5),
)


@dataclass
class _MutableClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


@dataclass(frozen=True, slots=True)
class LedgerFixture:
    """A migrated database, an engine, and one seeded QUEUED delivery."""

    engine: Engine
    db_path: Path
    delivery_id: DeliveryId
    session_factory: Callable[[], Session]

    def uow_factory(self) -> Callable[[], SqlAlchemyUnitOfWork]:
        return lambda: SqlAlchemyUnitOfWork(self.session_factory())

    def attempts(self) -> list[DeliveryAttempt]:
        """Read the whole ledger for this delivery through a fresh session."""
        session = self.session_factory()
        try:
            return DeliveryAttemptRepository(session).list_for_delivery(
                self.delivery_id, MAX_DELIVERY_ATTEMPT_HISTORY_LIMIT
            )
        finally:
            session.close()

    def attempt(self) -> DeliveryAttempt:
        found = self.attempts()
        assert len(found) == 1, f"expected exactly one attempt, got {len(found)}"
        return found[0]

    def delivery(self) -> OutboundDelivery:
        session = self.session_factory()
        try:
            stored = OutboundDeliveryRepository(session).get(self.delivery_id)
            assert stored is not None
            return stored
        finally:
            session.close()

    def raw_attempt_rows(self) -> list[tuple[object, ...]]:
        session = self.session_factory()
        try:
            return [
                tuple(row)
                for row in session.execute(
                    text(
                        "SELECT id, delivery_id, claim_generation, started_at, finished_at, "
                        "outcome, failure_code FROM delivery_attempts "
                        "WHERE delivery_id = :delivery_id ORDER BY started_at DESC, id DESC"
                    ),
                    {"delivery_id": str(self.delivery_id)},
                )
            ]
        finally:
            session.close()


def _seed(session: Session) -> DeliveryId:
    task = Task.new(id=TaskId.new(), title="t", description="d", created_at=T0)
    TaskRepository(session).add(task)
    session.flush()
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
    RunRepository(session).add(run)
    session.flush()
    invocation = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=run.id,
        tool_name="message.send",
        requested_input=None,
        requested_at=T0,
    )
    ToolInvocationRepository(session).add(invocation)
    session.flush()
    delivery = OutboundDelivery.new(
        id=DeliveryId.new(),
        source_kind=DeliverySourceKind.AGENT_REQUEST,
        source_run_id=run.id,
        source_tool_invocation_id=invocation.id,
        route_id="personal.notifications",
        route_fingerprint=FINGERPRINT,
        body="hello",
        available_at=T0,
        created_at=T0,
    )
    OutboundDeliveryRepository(session).add(delivery)
    session.commit()
    return delivery.id


def _migrated_engine(db_path: Path) -> Engine:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(config, "head")
    return create_engine(f"sqlite:///{db_path}")


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[LedgerFixture]:
    db_path = tmp_path / "delivery-attempts.db"
    engine = _migrated_engine(db_path)
    session_factory = create_session_factory(engine)
    seed = session_factory()
    try:
        delivery_id = _seed(seed)
    finally:
        seed.close()
    try:
        yield LedgerFixture(
            engine=engine,
            db_path=db_path,
            delivery_id=delivery_id,
            session_factory=session_factory,
        )
    finally:
        engine.dispose()


def _claimed(ledger: LedgerFixture, clock: _MutableClock) -> DeliveryClaim:
    claim = ClaimNextDelivery(
        ledger.uow_factory(),
        clock,
        RETRY_POLICY,
        worker_id="worker-a",
        lease_duration=LEASE,
        candidate_limit=5,
    ).execute()
    assert claim is not None
    return claim


def _dispatched(ledger: LedgerFixture, clock: _MutableClock) -> DeliveryClaim:
    claim = _claimed(ledger, clock)
    clock.value = BOUNDARY
    BeginDeliveryAttempt(ledger.uow_factory(), clock).execute(claim)
    return claim


# --------------------------------------------------------------------------
# BeginDeliveryAttempt: one committed transaction, observed from outside
# --------------------------------------------------------------------------


def test_begin_commits_the_marker_and_exactly_one_in_progress_attempt(
    ledger: LedgerFixture,
) -> None:
    clock = _MutableClock(T0)
    claim = _claimed(ledger, clock)
    clock.value = BOUNDARY

    boundary = BeginDeliveryAttempt(ledger.uow_factory(), clock).execute(claim)

    # A separate session must see both writes, or they were not committed.
    assert boundary == BOUNDARY
    assert ledger.delivery().dispatch_started_at == BOUNDARY
    attempt = ledger.attempt()
    assert attempt.outcome is DeliveryAttemptOutcome.IN_PROGRESS
    assert attempt.delivery_id == ledger.delivery_id
    assert attempt.claim_generation == claim.claim_generation
    assert attempt.started_at == BOUNDARY
    assert attempt.finished_at is None and attempt.failure_code is None


def test_begin_persists_no_endpoint_body_or_secret(ledger: LedgerFixture) -> None:
    """The stored row contains only the boundary facts, nothing about the send."""
    clock = _MutableClock(T0)
    _dispatched(ledger, clock)

    row = ledger.raw_attempt_rows()[0]
    stored = " ".join(str(value) for value in row)
    delivery = ledger.delivery()
    assert delivery.body not in stored
    assert delivery.route_id not in stored
    assert FINGERPRINT not in stored


@pytest.mark.parametrize(
    "stale",
    [
        lambda claim: replace(claim, worker_id="worker-b"),
        lambda claim: replace(claim, claim_token="other-token"),
        lambda claim: replace(claim, claim_generation=claim.claim_generation + 1),
    ],
    ids=["wrong_owner", "wrong_token", "stale_generation"],
)
def test_a_stale_claim_writes_neither_marker_nor_attempt(
    ledger: LedgerFixture, stale: object
) -> None:
    clock = _MutableClock(T0)
    claim = _claimed(ledger, clock)

    with pytest.raises(ClaimLost):
        BeginDeliveryAttempt(ledger.uow_factory(), clock).execute(stale(claim))  # type: ignore[operator]

    assert ledger.delivery().dispatch_started_at is None
    assert ledger.attempts() == []


def test_an_expired_claim_writes_neither_marker_nor_attempt(ledger: LedgerFixture) -> None:
    clock = _MutableClock(T0)
    claim = _claimed(ledger, clock)
    clock.value = claim.lease_expires_at

    with pytest.raises(ClaimLost):
        BeginDeliveryAttempt(ledger.uow_factory(), clock).execute(claim)

    assert ledger.delivery().dispatch_started_at is None
    assert ledger.attempts() == []


def test_begin_for_claim_is_fenced_on_every_component_of_the_claim(
    ledger: LedgerFixture,
) -> None:
    """The repository primitive itself refuses, without needing the use case."""
    clock = _MutableClock(T0)
    claim = _dispatched(ledger, clock)
    session = ledger.session_factory()
    repo = DeliveryAttemptRepository(session)
    try:
        for worker, token, generation, started_at in [
            ("worker-b", claim.claim_token, claim.claim_generation, BOUNDARY),
            ("worker-a", "other-token", claim.claim_generation, BOUNDARY),
            ("worker-a", claim.claim_token, claim.claim_generation + 1, BOUNDARY),
            # Right claim, wrong boundary: not the crossing this row records.
            ("worker-a", claim.claim_token, claim.claim_generation, BOUNDARY + timedelta(1)),
        ]:
            assert not repo.begin_for_claim(
                DeliveryAttemptId.new(),
                ledger.delivery_id,
                worker,
                token,
                generation,
                started_at,
                BOUNDARY,
            )
        session.commit()
    finally:
        session.close()

    assert len(ledger.attempts()) == 1


def test_begin_for_claim_refuses_an_unclaimed_delivery(ledger: LedgerFixture) -> None:
    session = ledger.session_factory()
    try:
        assert not DeliveryAttemptRepository(session).begin_for_claim(
            DeliveryAttemptId.new(), ledger.delivery_id, "worker-a", "token", 1, T0, T0
        )
        session.commit()
    finally:
        session.close()

    assert ledger.attempts() == []


def test_begin_for_claim_refuses_a_delivery_that_never_crossed_the_boundary(
    ledger: LedgerFixture,
) -> None:
    clock = _MutableClock(T0)
    claim = _claimed(ledger, clock)
    session = ledger.session_factory()
    try:
        assert not DeliveryAttemptRepository(session).begin_for_claim(
            DeliveryAttemptId.new(),
            ledger.delivery_id,
            claim.worker_id,
            claim.claim_token,
            claim.claim_generation,
            T0,
            T0,
        )
        session.commit()
    finally:
        session.close()

    assert ledger.attempts() == []


def test_the_repository_exposes_no_generic_insert_or_save(ledger: LedgerFixture) -> None:
    """The only creation path is begin_for_claim."""
    session = ledger.session_factory()
    try:
        repo = DeliveryAttemptRepository(session)
        assert not hasattr(repo, "add")
        assert not hasattr(repo, "save")
        assert not hasattr(repo, "merge")
    finally:
        session.close()


def test_duplicate_begin_for_one_generation_yields_exactly_one_attempt(
    ledger: LedgerFixture,
) -> None:
    """UNIQUE(delivery_id, claim_generation) is the database race fence."""
    clock = _MutableClock(T0)
    claim = _dispatched(ledger, clock)

    session = ledger.session_factory()
    try:
        with pytest.raises(IntegrityError):
            DeliveryAttemptRepository(session).begin_for_claim(
                DeliveryAttemptId.new(),
                ledger.delivery_id,
                claim.worker_id,
                claim.claim_token,
                claim.claim_generation,
                BOUNDARY,
                BOUNDARY,
            )
            session.flush()
        session.rollback()
    finally:
        session.close()

    assert len(ledger.attempts()) == 1


def test_concurrent_begin_over_two_sessions_yields_exactly_one_attempt(
    ledger: LedgerFixture,
) -> None:
    clock = _MutableClock(T0)
    claim = _claimed(ledger, clock)
    clock.value = BOUNDARY
    begin = BeginDeliveryAttempt(ledger.uow_factory(), clock)

    assert begin.execute(claim) == BOUNDARY
    # A second worker replaying the same claim is fenced by the one-way marker.
    with pytest.raises(ClaimLost):
        begin.execute(claim)

    assert len(ledger.attempts()) == 1


# --------------------------------------------------------------------------
# Terminal outcomes: delivery status and attempt outcome move together
# --------------------------------------------------------------------------


def test_success_commits_delivered_delivery_and_delivered_attempt(
    ledger: LedgerFixture,
) -> None:
    clock = _MutableClock(T0)
    claim = _dispatched(ledger, clock)
    clock.value = BOUNDARY + timedelta(seconds=1)

    PersistDeliveryOutcome(ledger.uow_factory(), clock).deliver(claim, provider_message_id="p-1")

    assert ledger.delivery().status is DeliveryStatus.DELIVERED
    attempt = ledger.attempt()
    assert attempt.outcome is DeliveryAttemptOutcome.DELIVERED
    assert attempt.finished_at == clock.value
    assert attempt.failure_code is None


def test_definite_failure_commits_failed_delivery_and_failed_attempt(
    ledger: LedgerFixture,
) -> None:
    clock = _MutableClock(T0)
    claim = _dispatched(ledger, clock)
    clock.value = BOUNDARY + timedelta(seconds=1)

    PersistDeliveryOutcome(ledger.uow_factory(), clock).fail(
        claim, failure_code="webhook_http_4xx", failure_message="Webhook delivery failed."
    )

    delivery = ledger.delivery()
    assert delivery.status is DeliveryStatus.FAILED
    assert delivery.failure_code == "webhook_http_4xx"
    attempt = ledger.attempt()
    assert attempt.outcome is DeliveryAttemptOutcome.FAILED
    assert attempt.failure_code == "webhook_http_4xx"
    assert attempt.finished_at == clock.value


def test_ambiguous_transport_commits_ambiguous_delivery_and_ambiguous_attempt(
    ledger: LedgerFixture,
) -> None:
    clock = _MutableClock(T0)
    claim = _dispatched(ledger, clock)
    clock.value = BOUNDARY + timedelta(seconds=1)

    PersistDeliveryOutcome(ledger.uow_factory(), clock).mark_ambiguous(
        claim, failure_code="webhook_timeout", failure_message="Outcome unknown."
    )

    delivery = ledger.delivery()
    assert delivery.status is DeliveryStatus.AMBIGUOUS
    assert delivery.dispatch_started_at == BOUNDARY
    attempt = ledger.attempt()
    assert attempt.outcome is DeliveryAttemptOutcome.AMBIGUOUS
    assert attempt.failure_code == "webhook_timeout"


def test_an_invalid_terminal_shape_commits_nothing(ledger: LedgerFixture) -> None:
    """Free-form transport text is rejected before any row changes."""
    clock = _MutableClock(T0)
    claim = _dispatched(ledger, clock)
    before_delivery = ledger.delivery()
    clock.value = BOUNDARY + timedelta(seconds=1)

    with pytest.raises(DomainValidationError):
        PersistDeliveryOutcome(ledger.uow_factory(), clock).fail(
            claim,
            failure_code="Connection refused to https://hook.test/secret",
            failure_message="m",
        )

    after = ledger.delivery()
    assert after.status is DeliveryStatus.SENDING
    assert after.failure_code == before_delivery.failure_code
    assert after.updated_at == before_delivery.updated_at
    assert ledger.attempt().outcome is DeliveryAttemptOutcome.IN_PROGRESS


@pytest.mark.parametrize(
    "stale",
    [
        lambda claim: replace(claim, worker_id="worker-b"),
        lambda claim: replace(claim, claim_token="other-token"),
        lambda claim: replace(claim, claim_generation=claim.claim_generation + 1),
    ],
    ids=["wrong_owner", "wrong_token", "stale_generation"],
)
def test_a_stale_claim_cannot_close_the_attempt(ledger: LedgerFixture, stale: object) -> None:
    clock = _MutableClock(T0)
    claim = _dispatched(ledger, clock)
    clock.value = BOUNDARY + timedelta(seconds=1)

    with pytest.raises(ClaimLost):
        PersistDeliveryOutcome(ledger.uow_factory(), clock).fail(
            stale(claim),  # type: ignore[operator]
            failure_code="webhook_http_4xx",
            failure_message="m",
        )

    assert ledger.delivery().status is DeliveryStatus.SENDING
    assert ledger.attempt().outcome is DeliveryAttemptOutcome.IN_PROGRESS


def test_a_stale_generation_cannot_close_another_generations_attempt(
    ledger: LedgerFixture,
) -> None:
    """Generation N's worker must not be able to close generation M's row.

    Generation 1 expires pre-dispatch (so it opens no attempt) and the row is
    reclaimed as generation 2, which then dispatches. Generation 1's stale
    worker must not be able to close generation 2's ledger row.
    """
    clock = _MutableClock(T0)
    first = _claimed(ledger, clock)

    clock.value = first.lease_expires_at + timedelta(seconds=1)
    claimer = ClaimNextDelivery(
        ledger.uow_factory(),
        clock,
        RETRY_POLICY,
        worker_id="worker-b",
        lease_duration=LEASE,
        candidate_limit=5,
    )
    assert claimer.execute() is None  # this tick only recovers to QUEUED
    clock.value = ledger.delivery().available_at
    second = claimer.execute()
    assert second is not None and second.claim_generation == 2
    boundary_two = clock.value + timedelta(seconds=1)
    clock.value = boundary_two
    BeginDeliveryAttempt(ledger.uow_factory(), clock).execute(second)
    assert ledger.attempt().claim_generation == 2

    clock.value = boundary_two + timedelta(seconds=1)
    with pytest.raises(ClaimLost):
        PersistDeliveryOutcome(ledger.uow_factory(), clock).fail(
            first, failure_code="webhook_http_4xx", failure_message="m"
        )

    survivor = ledger.attempt()
    assert survivor.claim_generation == 2
    assert survivor.outcome is DeliveryAttemptOutcome.IN_PROGRESS
    assert ledger.delivery().status is DeliveryStatus.SENDING


# --------------------------------------------------------------------------
# Crash / restart recovery
# --------------------------------------------------------------------------


def test_in_progress_attempt_survives_restart_and_recovers_to_ambiguous(
    ledger: LedgerFixture,
) -> None:
    clock = _MutableClock(T0)
    claim = _dispatched(ledger, clock)
    opened = ledger.attempt()

    # Drop the engine entirely, exactly like a worker crash mid-dispatch.
    ledger.engine.dispose()
    restarted = create_engine(f"sqlite:///{ledger.db_path}")
    restarted_factory = create_session_factory(restarted)
    try:
        session = restarted_factory()
        try:
            survivor = DeliveryAttemptRepository(session).get_for_generation(
                ledger.delivery_id, claim.claim_generation
            )
            assert survivor is not None
            assert survivor.id == opened.id
            assert survivor.outcome is DeliveryAttemptOutcome.IN_PROGRESS
            assert survivor.started_at == BOUNDARY
            delivery = OutboundDeliveryRepository(session).get(ledger.delivery_id)
            assert delivery is not None and delivery.status is DeliveryStatus.SENDING
        finally:
            session.close()

        recovered_at = claim.lease_expires_at + timedelta(seconds=1)

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(restarted_factory())

        with uow_factory() as uow:
            assert (
                RecoverExpiredDeliveryClaims(RETRY_POLICY, candidate_limit=5).execute(
                    uow, recovered_at
                )
                == 1
            )
            uow.commit()

        session = restarted_factory()
        try:
            delivery = OutboundDeliveryRepository(session).get(ledger.delivery_id)
            assert delivery is not None
            assert delivery.status is DeliveryStatus.AMBIGUOUS
            assert delivery.failure_code == LEASE_EXPIRED_AFTER_DISPATCH
            closed = DeliveryAttemptRepository(session).get_for_generation(
                ledger.delivery_id, claim.claim_generation
            )
            assert closed is not None
            # The same audit row, closed — recovery never invents a new one.
            assert closed.id == opened.id
            assert closed.started_at == BOUNDARY
            assert closed.outcome is DeliveryAttemptOutcome.AMBIGUOUS
            assert closed.failure_code == LEASE_EXPIRED_AFTER_DISPATCH
            assert closed.finished_at == recovered_at
            assert DeliveryAttemptRepository(session).list_for_delivery(ledger.delivery_id, 10) == [
                closed
            ]
        finally:
            session.close()
    finally:
        restarted.dispose()


def test_post_dispatch_recovery_never_requeues_for_automatic_retry(
    ledger: LedgerFixture,
) -> None:
    """Regression: no FAILED/AMBIGUOUS -> QUEUED, so no automatic resend."""
    clock = _MutableClock(T0)
    claim = _dispatched(ledger, clock)

    clock.value = claim.lease_expires_at + timedelta(seconds=1)
    claimer = ClaimNextDelivery(
        ledger.uow_factory(),
        clock,
        RETRY_POLICY,
        worker_id="worker-b",
        lease_duration=LEASE,
        candidate_limit=5,
    )
    assert claimer.execute() is None
    clock.value = clock.value + timedelta(days=365)
    assert claimer.execute() is None

    assert ledger.delivery().status is DeliveryStatus.AMBIGUOUS
    assert len(ledger.attempts()) == 1


def test_pre_dispatch_recovery_leaves_the_ledger_empty(ledger: LedgerFixture) -> None:
    """Regression: pre-dispatch retry is unchanged and audits nothing."""
    clock = _MutableClock(T0)
    claim = _claimed(ledger, clock)

    recovered_at = claim.lease_expires_at + timedelta(seconds=1)
    with ledger.uow_factory()() as uow:
        assert (
            RecoverExpiredDeliveryClaims(RETRY_POLICY, candidate_limit=5).execute(uow, recovered_at)
            == 1
        )
        uow.commit()

    assert ledger.delivery().status is DeliveryStatus.QUEUED
    assert ledger.attempts() == []


# --------------------------------------------------------------------------
# Database-enforced lifecycle shape
# --------------------------------------------------------------------------


def _raw_insert(ledger: LedgerFixture, **values: object) -> None:
    session = ledger.session_factory()
    try:
        session.execute(
            text(
                "INSERT INTO delivery_attempts "
                "(id, delivery_id, claim_generation, started_at, finished_at, outcome, "
                "failure_code) VALUES (:id, :delivery_id, :claim_generation, :started_at, "
                ":finished_at, :outcome, :failure_code)"
            ),
            {
                "id": str(uuid.uuid4()),
                "delivery_id": str(ledger.delivery_id),
                "claim_generation": 1,
                "started_at": "2026-01-01 00:00:05.000000",
                "finished_at": None,
                "outcome": "in_progress",
                "failure_code": None,
                **values,
            },
        )
        session.commit()
    finally:
        session.close()


@pytest.mark.parametrize(
    "values",
    [
        {"outcome": "in_progress", "finished_at": "2026-01-01 00:00:06.000000"},
        {"outcome": "in_progress", "failure_code": "webhook_timeout"},
        {
            "outcome": "delivered",
            "finished_at": "2026-01-01 00:00:06.000000",
            "failure_code": "webhook_timeout",
        },
        {"outcome": "delivered", "finished_at": None},
        {"outcome": "failed", "finished_at": "2026-01-01 00:00:06.000000"},
        {"outcome": "ambiguous", "finished_at": "2026-01-01 00:00:06.000000"},
        {
            "outcome": "failed",
            "finished_at": "2026-01-01 00:00:04.000000",
            "failure_code": "webhook_timeout",
        },
        {"outcome": "retrying", "finished_at": None},
        {"outcome": "in_progress", "claim_generation": 0},
        {
            "outcome": "failed",
            "finished_at": "2026-01-01 00:00:06.000000",
            "failure_code": "Webhook_Timeout",
        },
        {
            "outcome": "failed",
            "finished_at": "2026-01-01 00:00:06.000000",
            "failure_code": "connection refused to https://hook.test/secret",
        },
        {
            "outcome": "failed",
            "finished_at": "2026-01-01 00:00:06.000000",
            "failure_code": "webhook-timeout",
        },
        {
            "outcome": "failed",
            "finished_at": "2026-01-01 00:00:06.000000",
            "failure_code": "",
        },
        {
            "outcome": "failed",
            "finished_at": "2026-01-01 00:00:06.000000",
            "failure_code": "x" * 129,
        },
    ],
    ids=[
        "in_progress_finished",
        "in_progress_coded",
        "delivered_coded",
        "delivered_unfinished",
        "failed_uncoded",
        "ambiguous_uncoded",
        "finished_before_started",
        "unknown_outcome",
        "zero_generation",
        "uppercase_code",
        "endpoint_text_code",
        "dashed_code",
        "empty_code",
        "over_long_code",
    ],
)
def test_sqlite_rejects_an_impossible_attempt_shape(
    ledger: LedgerFixture, values: dict[str, object]
) -> None:
    """The CHECK constraints, not just the domain, refuse invalid audit rows."""
    with pytest.raises(IntegrityError):
        _raw_insert(ledger, **values)

    assert ledger.attempts() == []


def test_sqlite_accepts_the_legal_terminal_shapes(ledger: LedgerFixture) -> None:
    _raw_insert(
        ledger,
        claim_generation=1,
        outcome="failed",
        finished_at="2026-01-01 00:00:05.000000",
        failure_code="x" * 128,
    )
    _raw_insert(
        ledger, claim_generation=2, outcome="delivered", finished_at="2026-01-01 00:00:06.000000"
    )
    _raw_insert(ledger, claim_generation=3, outcome="in_progress")

    assert {a.outcome for a in ledger.attempts()} == {
        DeliveryAttemptOutcome.FAILED,
        DeliveryAttemptOutcome.DELIVERED,
        DeliveryAttemptOutcome.IN_PROGRESS,
    }


# --------------------------------------------------------------------------
# Bounded, deterministic history reads
# --------------------------------------------------------------------------


def test_history_is_ordered_by_started_at_desc_then_id_desc(ledger: LedgerFixture) -> None:
    tied = sorted((str(uuid.uuid4()) for _ in range(2)), reverse=True)
    _raw_insert(ledger, id=tied[0], claim_generation=1, started_at="2026-01-01 00:00:09.000000")
    _raw_insert(ledger, id=tied[1], claim_generation=2, started_at="2026-01-01 00:00:09.000000")
    _raw_insert(ledger, claim_generation=3, started_at="2026-01-01 00:00:01.000000")

    history = ledger.attempts()
    assert [(a.started_at, str(a.id)) for a in history] == sorted(
        ((a.started_at, str(a.id)) for a in history), reverse=True
    )
    assert [str(a.id) for a in history[:2]] == tied
    assert history[-1].claim_generation == 3


def test_history_respects_a_bounded_limit(ledger: LedgerFixture) -> None:
    for generation in range(1, 4):
        _raw_insert(
            ledger,
            claim_generation=generation,
            started_at=f"2026-01-01 00:00:0{generation}.000000",
        )

    session = ledger.session_factory()
    try:
        repo = DeliveryAttemptRepository(session)
        assert len(repo.list_for_delivery(ledger.delivery_id, 1)) == 1
        assert len(repo.list_for_delivery(ledger.delivery_id, 2)) == 2
        assert len(repo.list_for_delivery(ledger.delivery_id, 3)) == 3
        assert (
            len(repo.list_for_delivery(ledger.delivery_id, MAX_DELIVERY_ATTEMPT_HISTORY_LIMIT)) == 3
        )
    finally:
        session.close()


@pytest.mark.parametrize("limit", [0, -1, -1000, MAX_DELIVERY_ATTEMPT_HISTORY_LIMIT + 1], ids=str)
def test_history_rejects_an_out_of_range_limit(ledger: LedgerFixture, limit: int) -> None:
    """A negative LIMIT is unbounded in SQLite; it must never reach the query."""
    for generation in range(1, 4):
        _raw_insert(
            ledger,
            claim_generation=generation,
            started_at=f"2026-01-01 00:00:0{generation}.000000",
        )

    session = ledger.session_factory()
    try:
        with pytest.raises(ValueError, match="between 1 and"):
            DeliveryAttemptRepository(session).list_for_delivery(ledger.delivery_id, limit)
    finally:
        session.close()


def test_history_is_scoped_to_one_delivery(ledger: LedgerFixture) -> None:
    session = ledger.session_factory()
    try:
        other = _seed(session)
    finally:
        session.close()
    _raw_insert(ledger, claim_generation=1)

    session = ledger.session_factory()
    try:
        repo = DeliveryAttemptRepository(session)
        assert len(repo.list_for_delivery(ledger.delivery_id, 10)) == 1
        assert repo.list_for_delivery(other, 10) == []
    finally:
        session.close()


def test_reloaded_attempts_keep_their_invariants(ledger: LedgerFixture) -> None:
    """A SQLite round trip must not hand back a mutable audit record."""
    clock = _MutableClock(T0)
    claim = _dispatched(ledger, clock)
    clock.value = BOUNDARY + timedelta(seconds=1)
    PersistDeliveryOutcome(ledger.uow_factory(), clock).fail(
        claim, failure_code="webhook_http_5xx", failure_message="Webhook delivery failed."
    )

    reloaded = ledger.attempt()
    assert reloaded.started_at.tzinfo is UTC
    assert reloaded.finished_at is not None and reloaded.finished_at.tzinfo is UTC
    with pytest.raises(AttributeError, match="immutable"):
        reloaded.claim_generation = 99
    with pytest.raises(AttributeError, match="complete"):
        reloaded.outcome = DeliveryAttemptOutcome.DELIVERED
    with pytest.raises(AttributeError, match="complete"):
        reloaded.failure_code = "tampered"
    # And it is already terminal, so it can never be completed again.
    from friday.domain.errors import InvalidStateTransition

    with pytest.raises(InvalidStateTransition):
        reloaded.complete(
            outcome=DeliveryAttemptOutcome.DELIVERED, finished_at=clock.value + timedelta(1)
        )
