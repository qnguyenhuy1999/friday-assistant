from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from friday.application.delivery_lifecycle import (
    LEASE_EXPIRED_AFTER_DISPATCH,
    PRE_DISPATCH_ATTEMPTS_EXHAUSTED,
    ClaimNextDelivery,
    MarkDeliveryDispatchStarted,
    RecoverExpiredDeliveryClaims,
)
from friday.application.retry_policy import RetryPolicy
from friday.domain import (
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
from friday.domain.identifiers import ScheduleFireId, ScheduleId
from friday.infrastructure.persistence.database import create_engine as production_create_engine
from friday.infrastructure.persistence.database import create_session_factory
from friday.infrastructure.persistence.models import RunRow, ScheduleFireRow, ScheduleRow
from friday.infrastructure.persistence.repositories import (
    OutboundDeliveryRepository,
    RunRepository,
    TaskRepository,
    ToolInvocationRepository,
)
from friday.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork

T0 = datetime(2026, 1, 1, tzinfo=UTC)
FINGERPRINT = "a" * 64


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    """Acceptance tests use the actual Alembic-created SQLite schema."""
    db_path = tmp_path / "outbound-deliveries.db"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    db_session = sessionmaker(bind=engine)()
    try:
        yield db_session
    finally:
        db_session.close()
        engine.dispose()


def _run_and_invocation(session: Session) -> tuple[RunId, ToolInvocationId]:
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
    return run.id, invocation.id


def _delivery(
    run_id: RunId, invocation_id: ToolInvocationId, available_at: datetime
) -> OutboundDelivery:
    return OutboundDelivery.new(
        id=DeliveryId.new(),
        source_kind=DeliverySourceKind.AGENT_REQUEST,
        source_run_id=run_id,
        source_tool_invocation_id=invocation_id,
        route_id="personal.notifications",
        route_fingerprint=FINGERPRINT,
        body="hello",
        available_at=available_at,
        created_at=T0,
    )


def test_round_trip_save_and_list_due(session: Session) -> None:
    run_id, invocation_id = _run_and_invocation(session)
    repo = OutboundDeliveryRepository(session)
    delivery = _delivery(run_id, invocation_id, T0)
    repo.add(delivery)
    session.flush()

    generation = repo.try_claim(delivery.id, "worker", "token", T0, T0 + timedelta(minutes=1))
    assert generation == 1
    boundary = T0 + timedelta(seconds=1)
    assert repo.mark_dispatch_started(delivery.id, "worker", "token", generation, boundary)
    claimed = repo.get(delivery.id)
    assert claimed is not None
    claimed.mark_ambiguous(at=boundary, failure_code="timeout", failure_message="lost")
    assert repo.save_claimed_lifecycle(claimed, "worker", "token", generation, boundary)
    session.flush()
    fetched = repo.get(delivery.id)
    assert fetched is not None
    assert fetched.status is DeliveryStatus.AMBIGUOUS
    assert fetched.claim_token == "token"
    assert fetched.failure_code == "timeout"
    assert fetched.updated_at.tzinfo is UTC
    assert fetched.route_id == delivery.route_id
    assert fetched.route_fingerprint == delivery.route_fingerprint
    assert fetched.subject == delivery.subject
    assert fetched.body == delivery.body
    assert fetched.body_sha256 == delivery.body_sha256
    assert fetched.source_run_id == delivery.source_run_id
    assert fetched.source_tool_invocation_id == delivery.source_tool_invocation_id
    with pytest.raises(AttributeError):
        fetched.body = "retargeted"
    with pytest.raises(AttributeError):
        fetched.route_id = "retargeted.route"
    with pytest.raises(AttributeError):
        fetched.source_tool_invocation_id = ToolInvocationId.new()

    future_invocation = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=run_id,
        tool_name="message.send",
        requested_input=None,
        requested_at=T0,
    )
    ToolInvocationRepository(session).add(future_invocation)
    session.flush()
    future = _delivery(run_id, future_invocation.id, T0 + timedelta(minutes=1))
    repo.add(future)
    session.flush()
    assert repo.list_due(T0, 10) == []


def test_list_due_is_queued_only_and_deterministic(session: Session) -> None:
    run_id, invocation_a = _run_and_invocation(session)
    invocation_b = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=run_id,
        tool_name="message.send",
        requested_input=None,
        requested_at=T0,
    )
    ToolInvocationRepository(session).add(invocation_b)
    session.flush()
    repo = OutboundDeliveryRepository(session)
    later = _delivery(run_id, invocation_a, T0 + timedelta(seconds=1))
    earlier = _delivery(run_id, invocation_b.id, T0)
    repo.add(later)
    repo.add(earlier)
    session.flush()
    assert [delivery.id for delivery in repo.list_due(T0 + timedelta(seconds=1), 1)] == [earlier.id]


def test_source_tool_invocation_is_unique(session: Session) -> None:
    run_id, invocation_id = _run_and_invocation(session)
    repo = OutboundDeliveryRepository(session)
    repo.add(_delivery(run_id, invocation_id, T0))
    session.flush()
    repo.add(_delivery(run_id, invocation_id, T0))
    with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
        session.flush()


def test_source_schedule_fire_is_unique(session: Session) -> None:
    run_id, _ = _run_and_invocation(session)
    run = session.get(RunRow, str(run_id))
    assert run is not None
    schedule_id = ScheduleId.new()
    fire_id = ScheduleFireId.new()
    session.add(
        ScheduleRow(
            id=str(schedule_id),
            task_id=run.task_id,
            kind="once",
            cron=None,
            run_at=T0,
            timezone="UTC",
            status="completed",
            next_fire_at=None,
            created_at=T0,
            updated_at=T0,
        )
    )
    session.add(
        ScheduleFireRow(
            id=str(fire_id),
            schedule_id=str(schedule_id),
            scheduled_for=T0,
            fired_at=T0,
            run_id=str(run_id),
        )
    )
    session.flush()
    repo = OutboundDeliveryRepository(session)

    def scheduled() -> OutboundDelivery:
        return OutboundDelivery.new(
            id=DeliveryId.new(),
            source_kind=DeliverySourceKind.SCHEDULED_RUN_ANSWER,
            source_run_id=run_id,
            source_schedule_fire_id=fire_id,
            route_id="scheduled.route",
            route_fingerprint=FINGERPRINT,
            body="hello",
            available_at=T0,
            created_at=T0,
        )

    repo.add(scheduled())
    session.flush()
    repo.add(scheduled())
    with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
        session.flush()


# --- Claim fencing, dispatch boundary, and recovery over real SQLite ---------
#
# These use the production engine factory (foreign keys, WAL, busy timeout) and
# independent sessions so each actor commits separately, the way two worker
# processes would.

LEASE = timedelta(minutes=1)
RETRY_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=timedelta(seconds=30),
    multiplier=2.0,
    max_delay=timedelta(minutes=5),
)


@dataclass(frozen=True, slots=True)
class DeliveryFixture:
    """A migrated database plus three independent sessions over one engine."""

    engine: Engine
    seed: Session
    session_a: Session
    session_b: Session
    delivery_id: DeliveryId
    db_path: Path

    def repo(self, session: Session) -> OutboundDeliveryRepository:
        return OutboundDeliveryRepository(session)

    def read(self, session: Session | None = None) -> OutboundDelivery:
        target = session if session is not None else self.seed
        target.expire_all()
        delivery = OutboundDeliveryRepository(target).get(self.delivery_id)
        assert delivery is not None
        return delivery


def _migrated_engine(db_path: Path) -> Engine:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(config, "head")
    return production_create_engine(f"sqlite:///{db_path}")


@pytest.fixture
def fixture(tmp_path: Path) -> Iterator[DeliveryFixture]:
    db_path = tmp_path / "delivery-claims.db"
    engine = _migrated_engine(db_path)
    factory = create_session_factory(engine)
    seed, session_a, session_b = factory(), factory(), factory()
    try:
        run_id, invocation_id = _run_and_invocation(seed)
        delivery = _delivery(run_id, invocation_id, T0)
        OutboundDeliveryRepository(seed).add(delivery)
        seed.commit()
        yield DeliveryFixture(
            engine=engine,
            seed=seed,
            session_a=session_a,
            session_b=session_b,
            delivery_id=delivery.id,
            db_path=db_path,
        )
    finally:
        seed.close()
        session_a.close()
        session_b.close()
        engine.dispose()


def test_two_workers_racing_produce_exactly_one_claim(fixture: DeliveryFixture) -> None:
    generation_a = fixture.repo(fixture.session_a).try_claim(
        fixture.delivery_id, "worker-a", "token-a", T0, T0 + LEASE
    )
    assert generation_a == 1
    fixture.session_a.commit()

    generation_b = fixture.repo(fixture.session_b).try_claim(
        fixture.delivery_id, "worker-b", "token-b", T0, T0 + LEASE
    )
    assert generation_b is None

    stored = fixture.read()
    assert stored.status is DeliveryStatus.SENDING
    assert stored.claim_owner == "worker-a"
    # attempt_count increments exactly once for the winning claim.
    assert stored.attempt_count == 1
    assert stored.dispatch_started_at is None


def test_concurrent_claim_over_real_sqlite_produces_exactly_one_winner(
    fixture: DeliveryFixture,
) -> None:
    """Two threads race try_claim over the production engine, synchronized by
    a barrier immediately before the fenced UPDATE, so the race is genuine
    rather than simulated by sequential calls."""
    barrier = threading.Barrier(2)
    results: dict[str, int | None | BaseException] = {}
    session_factory = create_session_factory(fixture.engine)

    def attempt(worker: str, token: str) -> None:
        session = session_factory()
        try:
            repo = OutboundDeliveryRepository(session)
            barrier.wait(timeout=10)
            try:
                generation = repo.try_claim(fixture.delivery_id, worker, token, T0, T0 + LEASE)
                session.commit()
                results[worker] = generation
            except OperationalError as exc:  # pragma: no cover - failure path only
                session.rollback()
                results[worker] = exc
        finally:
            session.close()

    thread_a = threading.Thread(target=attempt, args=("worker-a", "token-a"))
    thread_b = threading.Thread(target=attempt, args=("worker-b", "token-b"))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=10)
    thread_b.join(timeout=10)

    for worker, result in results.items():
        assert not isinstance(result, BaseException), f"{worker} raised {result!r}"

    winners = [worker for worker, generation in results.items() if generation is not None]
    losers = [worker for worker, generation in results.items() if generation is None]
    assert len(winners) == 1
    assert len(losers) == 1

    stored = fixture.read()
    assert stored.status is DeliveryStatus.SENDING
    assert stored.attempt_count == 1
    assert stored.claim_generation == 1
    assert stored.claim_owner == winners[0]
    assert stored.claim_token == ("token-a" if winners[0] == "worker-a" else "token-b")


def test_post_dispatch_claim_cannot_be_forced_to_queued_via_any_write_primitive(
    fixture: DeliveryFixture,
) -> None:
    """Finding 1 hardening: every requeue-shaped write primitive is gated on
    dispatch_started_at IS NULL, so a post-dispatch claim can never be forced
    back to QUEUED."""
    repo = fixture.repo(fixture.session_a)
    generation = repo.try_claim(fixture.delivery_id, "worker-a", "token-a", T0, T0 + LEASE)
    assert generation == 1
    boundary = T0 + timedelta(seconds=1)
    assert repo.mark_dispatch_started(
        fixture.delivery_id, "worker-a", "token-a", generation, boundary
    )
    fixture.session_a.commit()

    claimed = fixture.read()
    forced_queued = _forced_status(claimed, DeliveryStatus.QUEUED)
    assert not repo.save_claimed_lifecycle(
        forced_queued, "worker-a", "token-a", generation, boundary
    )

    expired_at = boundary + LEASE + timedelta(seconds=1)
    assert not repo.requeue_expired_pre_dispatch(
        fixture.delivery_id, generation, expired_at, expired_at
    )
    assert not repo.fail_expired_pre_dispatch(fixture.delivery_id, generation, expired_at, "c", "m")

    stored = fixture.read()
    assert stored.status is DeliveryStatus.SENDING
    assert stored.dispatch_started_at == boundary


def test_claim_generation_increments_monotonically(fixture: DeliveryFixture) -> None:
    generations: list[int] = []
    now = T0
    for index in range(3):
        claimed = fixture.repo(fixture.session_a).try_claim(
            fixture.delivery_id, f"worker-{index}", f"token-{index}", now, now + LEASE
        )
        assert claimed is not None
        generations.append(claimed)
        fixture.session_a.commit()

        # Recover the expired pre-dispatch lease so the row is claimable again.
        now = now + LEASE + timedelta(seconds=1)
        assert fixture.repo(fixture.session_a).requeue_expired_pre_dispatch(
            fixture.delivery_id, claimed, now, now
        )
        fixture.session_a.commit()

    assert generations == [1, 2, 3]
    assert fixture.read().attempt_count == 3


def test_future_available_at_delivery_cannot_be_claimed(fixture: DeliveryFixture) -> None:
    future = T0 + timedelta(minutes=5)
    assert (
        fixture.repo(fixture.session_a).requeue_expired_pre_dispatch(
            fixture.delivery_id, 0, T0, future
        )
        is False
    )  # not SENDING yet

    generation = fixture.repo(fixture.session_a).try_claim(
        fixture.delivery_id, "worker-a", "token-a", T0, T0 + LEASE
    )
    assert generation == 1
    fixture.session_a.commit()
    expired_at = T0 + LEASE + timedelta(seconds=1)
    assert fixture.repo(fixture.session_a).requeue_expired_pre_dispatch(
        fixture.delivery_id, generation, expired_at, future
    )
    fixture.session_a.commit()

    assert fixture.repo(fixture.session_b).list_due(expired_at, 10) == []
    assert (
        fixture.repo(fixture.session_b).try_claim(
            fixture.delivery_id, "worker-b", "token-b", expired_at, expired_at + LEASE
        )
        is None
    )
    assert [item.id for item in fixture.repo(fixture.session_b).list_due(future, 10)] == [
        fixture.delivery_id
    ]


def test_stale_worker_and_generation_cannot_mark_dispatch_started(
    fixture: DeliveryFixture,
) -> None:
    repo_a = fixture.repo(fixture.session_a)
    generation = repo_a.try_claim(fixture.delivery_id, "worker-a", "token-a", T0, T0 + LEASE)
    assert generation == 1
    fixture.session_a.commit()

    repo_b = fixture.repo(fixture.session_b)
    for worker, token, gen in [
        ("worker-b", "token-a", generation),
        ("worker-a", "token-b", generation),
        ("worker-a", "token-a", generation + 1),
    ]:
        assert not repo_b.mark_dispatch_started(fixture.delivery_id, worker, token, gen, T0)
    # An expired lease fails closed too (equality is expired).
    assert not repo_b.mark_dispatch_started(
        fixture.delivery_id, "worker-a", "token-a", generation, T0 + LEASE
    )
    assert fixture.read(fixture.session_b).dispatch_started_at is None
    # A no-op UPDATE still opens a SQLite write transaction; a real worker's
    # short unit of work ends here, so release the lock before the owner writes.
    fixture.session_b.rollback()

    boundary = T0 + timedelta(seconds=10)
    assert repo_a.mark_dispatch_started(
        fixture.delivery_id, "worker-a", "token-a", generation, boundary
    )
    fixture.session_a.commit()
    assert fixture.read().dispatch_started_at == boundary
    # The boundary is crossed exactly once.
    assert not repo_a.mark_dispatch_started(
        fixture.delivery_id, "worker-a", "token-a", generation, boundary + timedelta(seconds=1)
    )
    assert fixture.read().dispatch_started_at == boundary


def test_dispatch_started_at_rejects_direct_mutation_after_sqlite_round_trip(
    fixture: DeliveryFixture,
) -> None:
    repo = fixture.repo(fixture.session_a)
    generation = repo.try_claim(fixture.delivery_id, "worker-a", "token-a", T0, T0 + LEASE)
    assert generation == 1
    boundary = T0 + timedelta(seconds=1)
    assert repo.mark_dispatch_started(
        fixture.delivery_id, "worker-a", "token-a", generation, boundary
    )
    fixture.session_a.commit()

    reloaded = fixture.read(fixture.session_b)
    assert reloaded.dispatch_started_at == boundary
    with pytest.raises(AttributeError):
        reloaded.dispatch_started_at = boundary + timedelta(seconds=1)
    assert reloaded.dispatch_started_at == boundary


def test_stale_worker_cannot_persist_an_outcome_after_recovery(
    fixture: DeliveryFixture,
) -> None:
    repo_a = fixture.repo(fixture.session_a)
    generation_one = repo_a.try_claim(fixture.delivery_id, "worker-a", "token-a", T0, T0 + LEASE)
    assert generation_one == 1
    fixture.session_a.commit()
    claimed = fixture.read()

    # Generation 1's lease expires and is recovered; generation 2 then claims.
    recovered_at = T0 + LEASE + timedelta(seconds=1)
    repo_b = fixture.repo(fixture.session_b)
    assert repo_b.requeue_expired_pre_dispatch(
        fixture.delivery_id, generation_one, recovered_at, recovered_at
    )
    generation_two = repo_b.try_claim(
        fixture.delivery_id, "worker-b", "token-b", recovered_at, recovered_at + LEASE
    )
    assert generation_two == 2
    fixture.session_b.commit()

    # Every generation-1 mutation now fails closed.
    assert not repo_a.is_claim_active(
        fixture.delivery_id, "worker-a", "token-a", generation_one, recovered_at
    )
    assert not repo_a.mark_dispatch_started(
        fixture.delivery_id, "worker-a", "token-a", generation_one, recovered_at
    )
    for status in (DeliveryStatus.DELIVERED, DeliveryStatus.FAILED, DeliveryStatus.AMBIGUOUS):
        stale = _forced_status(claimed, status)
        assert not repo_a.save_claimed_lifecycle(
            stale, "worker-a", "token-a", generation_one, recovered_at
        )
    stale_requeue = _forced_status(claimed, DeliveryStatus.QUEUED)
    assert not repo_a.save_claimed_lifecycle(
        stale_requeue, "worker-a", "token-a", generation_one, recovered_at
    )

    survivor = fixture.read()
    assert survivor.status is DeliveryStatus.SENDING
    assert survivor.claim_owner == "worker-b"
    assert survivor.claim_generation == 2


def test_fenced_outcome_never_rewrites_authority_or_content(fixture: DeliveryFixture) -> None:
    repo = fixture.repo(fixture.session_a)
    generation = repo.try_claim(fixture.delivery_id, "worker-a", "token-a", T0, T0 + LEASE)
    assert generation == 1
    boundary = T0 + timedelta(seconds=1)
    assert repo.mark_dispatch_started(
        fixture.delivery_id, "worker-a", "token-a", generation, boundary
    )
    fixture.session_a.commit()

    original = fixture.read()
    delivered = fixture.read()
    delivered.deliver(at=boundary + timedelta(seconds=1), provider_message_id="provider-1")
    # Smuggle retargeted authority past the aggregate's own guard: the fenced
    # UPDATE must still refuse to write those columns.
    object.__setattr__(delivered, "route_id", "attacker.route")
    object.__setattr__(delivered, "body", "attacker body")
    object.__setattr__(delivered, "body_sha256", "b" * 64)
    object.__setattr__(delivered, "subject", "attacker subject")
    object.__setattr__(delivered, "attempt_count", 99)
    object.__setattr__(delivered, "claim_generation", 99)

    assert repo.save_claimed_lifecycle(
        delivered, "worker-a", "token-a", generation, boundary + timedelta(seconds=1)
    )
    fixture.session_a.commit()

    stored = fixture.read()
    assert stored.status is DeliveryStatus.DELIVERED
    assert stored.provider_message_id == "provider-1"
    assert stored.route_id == original.route_id
    assert stored.route_fingerprint == original.route_fingerprint
    assert stored.subject == original.subject
    assert stored.body == original.body
    assert stored.body_sha256 == original.body_sha256
    assert stored.source_run_id == original.source_run_id
    assert stored.source_tool_invocation_id == original.source_tool_invocation_id
    assert stored.attempt_count == original.attempt_count
    assert stored.claim_generation == original.claim_generation
    assert stored.dispatch_started_at == boundary


def test_expired_pre_dispatch_claim_is_requeued_with_backoff(fixture: DeliveryFixture) -> None:
    uow_factory = _uow_factory(fixture.engine)
    clock = _MutableClock(T0)
    claim = ClaimNextDelivery(
        uow_factory,
        clock,
        RETRY_POLICY,
        worker_id="worker-a",
        lease_duration=LEASE,
        candidate_limit=5,
    ).execute()
    assert claim is not None
    assert claim.claim_generation == 1

    recovered_at = claim.lease_expires_at + timedelta(seconds=1)
    with uow_factory() as uow:
        assert (
            RecoverExpiredDeliveryClaims(RETRY_POLICY, candidate_limit=5).execute(uow, recovered_at)
            == 1
        )
        uow.commit()

    stored = fixture.read()
    assert stored.status is DeliveryStatus.QUEUED
    assert stored.available_at == recovered_at + RETRY_POLICY.compute_delay(2)
    assert (stored.attempt_count, stored.claim_generation) == (1, 1)
    assert stored.claim_owner is None and stored.claim_token is None
    assert stored.claim_expires_at is None
    assert stored.failure_code is None


def test_expired_post_dispatch_claim_becomes_ambiguous_and_is_never_reclaimed(
    fixture: DeliveryFixture,
) -> None:
    uow_factory = _uow_factory(fixture.engine)
    clock = _MutableClock(T0)
    claim = ClaimNextDelivery(
        uow_factory,
        clock,
        RETRY_POLICY,
        worker_id="worker-a",
        lease_duration=LEASE,
        candidate_limit=5,
    ).execute()
    assert claim is not None
    clock.value = T0 + timedelta(seconds=5)
    MarkDeliveryDispatchStarted(uow_factory, clock).execute(claim)

    recovered_at = claim.lease_expires_at + timedelta(seconds=1)
    with uow_factory() as uow:
        assert (
            RecoverExpiredDeliveryClaims(RETRY_POLICY, candidate_limit=5).execute(uow, recovered_at)
            == 1
        )
        uow.commit()

    stored = fixture.read()
    assert stored.status is DeliveryStatus.AMBIGUOUS
    assert stored.failure_code == LEASE_EXPIRED_AFTER_DISPATCH
    assert stored.dispatch_started_at == T0 + timedelta(seconds=5)

    # AMBIGUOUS is terminal: never listed as due, never claimable again.
    far_future = recovered_at + timedelta(days=30)
    assert fixture.repo(fixture.session_b).list_due(far_future, 10) == []
    assert (
        fixture.repo(fixture.session_b).try_claim(
            fixture.delivery_id, "worker-b", "token-b", far_future, far_future + LEASE
        )
        is None
    )
    clock.value = far_future
    assert (
        ClaimNextDelivery(
            uow_factory,
            clock,
            RETRY_POLICY,
            worker_id="worker-b",
            lease_duration=LEASE,
            candidate_limit=5,
        ).execute()
        is None
    )


def test_repeated_pre_dispatch_expiry_respects_max_attempts_then_fails(
    fixture: DeliveryFixture,
) -> None:
    uow_factory = _uow_factory(fixture.engine)
    clock = _MutableClock(T0)
    claimer = ClaimNextDelivery(
        uow_factory,
        clock,
        RETRY_POLICY,
        worker_id="worker-a",
        lease_duration=LEASE,
        candidate_limit=5,
    )

    for attempt in range(1, RETRY_POLICY.max_attempts + 1):
        claim = claimer.execute()
        assert claim is not None, f"attempt {attempt} should have been claimable"
        assert claim.claim_generation == attempt
        assert fixture.read().attempt_count == attempt

        # Expire the lease pre-dispatch; this tick only recovers.
        clock.value = claim.lease_expires_at + timedelta(seconds=1)
        assert claimer.execute() is None
        recovered = fixture.read()
        if attempt < RETRY_POLICY.max_attempts:
            assert recovered.status is DeliveryStatus.QUEUED
            clock.value = recovered.available_at
        else:
            assert recovered.status is DeliveryStatus.FAILED

    final = fixture.read()
    assert final.status is DeliveryStatus.FAILED
    assert final.failure_code == PRE_DISPATCH_ATTEMPTS_EXHAUSTED
    assert final.attempt_count == RETRY_POLICY.max_attempts
    assert final.dispatch_started_at is None
    clock.value = clock.value + timedelta(days=1)
    assert claimer.execute() is None


def test_state_and_recovery_survive_an_engine_restart(fixture: DeliveryFixture) -> None:
    repo = fixture.repo(fixture.session_a)
    generation = repo.try_claim(fixture.delivery_id, "worker-a", "token-a", T0, T0 + LEASE)
    assert generation == 1
    boundary = T0 + timedelta(seconds=5)
    assert repo.mark_dispatch_started(
        fixture.delivery_id, "worker-a", "token-a", generation, boundary
    )
    fixture.session_a.commit()

    # Drop every session and the engine, exactly like a worker crash.
    fixture.seed.close()
    fixture.session_a.close()
    fixture.session_b.close()
    fixture.engine.dispose()

    restarted = production_create_engine(f"sqlite:///{fixture.db_path}")
    restarted_factory = create_session_factory(restarted)
    try:
        session = restarted_factory()
        survivor = OutboundDeliveryRepository(session).get(fixture.delivery_id)
        assert survivor is not None
        assert survivor.status is DeliveryStatus.SENDING
        assert survivor.dispatch_started_at == boundary
        assert survivor.claim_generation == 1
        session.close()

        uow_factory = _uow_factory(restarted)
        recovered_at = T0 + LEASE + timedelta(seconds=1)
        with uow_factory() as uow:
            assert (
                RecoverExpiredDeliveryClaims(RETRY_POLICY, candidate_limit=5).execute(
                    uow, recovered_at
                )
                == 1
            )
            uow.commit()

        session = restarted_factory()
        recovered = OutboundDeliveryRepository(session).get(fixture.delivery_id)
        assert recovered is not None
        # Unchanged post-restart semantics: crossed boundary => AMBIGUOUS.
        assert recovered.status is DeliveryStatus.AMBIGUOUS
        assert recovered.failure_code == LEASE_EXPIRED_AFTER_DISPATCH
        session.close()
    finally:
        restarted.dispose()


def test_find_expired_claims_excludes_unexpired_and_non_sending_rows(
    fixture: DeliveryFixture,
) -> None:
    repo = fixture.repo(fixture.session_a)
    assert repo.find_expired_claims(T0 + timedelta(days=1), 10) == []

    generation = repo.try_claim(fixture.delivery_id, "worker-a", "token-a", T0, T0 + LEASE)
    assert generation == 1
    fixture.session_a.commit()
    assert repo.find_expired_claims(T0 + timedelta(seconds=1), 10) == []
    # Equality is expired.
    assert [item.id for item in repo.find_expired_claims(T0 + LEASE, 10)] == [fixture.delivery_id]


def _forced_status(delivery: OutboundDelivery, status: DeliveryStatus) -> OutboundDelivery:
    """A stale worker's in-memory aggregate carrying an outcome it may not write."""
    clone = deepcopy(delivery)
    object.__setattr__(clone, "status", status)
    return clone


def _uow_factory(engine: Engine) -> Callable[[], SqlAlchemyUnitOfWork]:
    """A real UnitOfWorkFactory: each call opens its own short transaction."""
    session_factory = create_session_factory(engine)
    return lambda: SqlAlchemyUnitOfWork(session_factory())


@dataclass
class _MutableClock:
    value: datetime

    def now(self) -> datetime:
        return self.value
