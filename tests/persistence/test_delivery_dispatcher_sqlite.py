"""SQLite integration tests for the dispatcher transaction boundaries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest

from friday.application.delivery_lifecycle import (
    LEASE_EXPIRED_AFTER_DISPATCH,
    BeginDeliveryAttempt,
    ClaimNextDelivery,
    MarkDeliveryDispatchStarted,
    PersistDeliveryOutcome,
)
from friday.application.ports import UnitOfWork
from friday.application.retry_policy import RetryPolicy
from friday.domain import DeliveryAttempt, DeliveryAttemptOutcome, Run, Task, ToolInvocation
from friday.domain.identifiers import DeliveryId, RunId, TaskId, ToolInvocationId
from friday.domain.outbound_delivery import DeliverySourceKind, DeliveryStatus, OutboundDelivery
from friday.infrastructure.messaging.config import MessagingRoute
from friday.infrastructure.messaging.dispatcher import (
    ROUTE_DISABLED,
    ROUTE_FINGERPRINT_MISMATCH,
    ROUTE_MISSING,
    TRANSPORT_EXCEPTION,
    DeliveryDispatcher,
    DispatchResult,
)
from friday.infrastructure.messaging.transport import DeliveryTransport
from friday.infrastructure.messaging.transport_models import TransportRequest, TransportResult
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.models import Base
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory
from tests.application.fakes import T0, FakeClock

RETRY = RetryPolicy(3, timedelta(seconds=1), 2.0, timedelta(seconds=30))
LEASE = timedelta(minutes=1)


def _route() -> MessagingRoute:
    return MessagingRoute(
        "ops.alerts",
        "Ops",
        True,
        "https_webhook",
        "ops",
        "ENDPOINT",
        "https://hook.test/secret",
        "body",
        1000,
        5.0,
    )


def _delivery(
    route: MessagingRoute, *, run_id: RunId, invocation_id: ToolInvocationId
) -> OutboundDelivery:
    return OutboundDelivery.new(
        id=DeliveryId.new(),
        source_kind=DeliverySourceKind.AGENT_REQUEST,
        source_run_id=run_id,
        source_tool_invocation_id=invocation_id,
        route_id=route.route_id,
        route_fingerprint=route.fingerprint,
        body="payload",
        available_at=T0,
        created_at=T0,
    )


def _seed(factory: Callable[[], UnitOfWork], route: MessagingRoute) -> OutboundDelivery:
    task = Task.new(id=TaskId.new(), title="t", description="d", created_at=T0)
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
    invocation = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=run.id,
        tool_name="message.send",
        requested_input=None,
        requested_at=T0,
    )
    delivery = _delivery(route, run_id=run.id, invocation_id=invocation.id)
    with factory() as uow:
        uow.tasks.add(task)
        uow.commit()
    with factory() as uow:
        uow.runs.add(run)
        uow.commit()
    with factory() as uow:
        uow.tool_invocations.add(invocation)
        uow.commit()
    with factory() as uow:
        uow.deliveries.add(delivery)
        uow.commit()
    return delivery


def test_sqlite_dispatcher_commits_marker_before_transport(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'dispatcher.db'}")
    Base.metadata.create_all(engine)
    factory = create_unit_of_work_factory(create_session_factory(engine))
    clock, route = FakeClock(T0), _route()
    delivery = _seed(factory, route)

    class InspectingTransport:
        def send(self, request: TransportRequest) -> TransportResult:
            with factory() as uow:
                stored = uow.deliveries.get(delivery.id)
                assert stored is not None and stored.dispatch_started_at is not None
            return TransportResult.delivered()

    dispatcher = DeliveryDispatcher(
        ClaimNextDelivery(
            factory,
            clock,
            RETRY,
            worker_id="worker",
            lease_duration=LEASE,
            candidate_limit=10,
        ),
        MarkDeliveryDispatchStarted(factory, clock),
        PersistDeliveryOutcome(factory, clock),
        factory,
        (route,),
        InspectingTransport(),
    )
    assert dispatcher.dispatch_once() is DispatchResult.DELIVERED
    with factory() as uow:
        stored = uow.deliveries.get(delivery.id)
        assert stored is not None and stored.status is DeliveryStatus.DELIVERED
    engine.dispose()


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (lambda route: (), ROUTE_MISSING),
        (lambda route: (replace(route, enabled=False),), ROUTE_DISABLED),
        (
            lambda route: (replace(route, endpoint="https://hook.test/rotated"),),
            ROUTE_FINGERPRINT_MISMATCH,
        ),
    ],
    ids=["missing", "disabled", "fingerprint_mismatch"],
)
def test_sqlite_route_authority_drift_parks_ambiguous_before_dispatch(
    tmp_path: Path,
    configured: Callable[[MessagingRoute], tuple[MessagingRoute, ...]],
    expected: str,
) -> None:
    """Durably, all three drift cases land AMBIGUOUS with no dispatch marker."""
    engine = create_engine(f"sqlite:///{tmp_path / 'drift.db'}")
    Base.metadata.create_all(engine)
    factory = create_unit_of_work_factory(create_session_factory(engine))
    clock, route = FakeClock(T0), _route()
    delivery = _seed(factory, route)

    class UnreachableTransport:
        def send(self, request: TransportRequest) -> TransportResult:
            raise AssertionError("route authority drift must never touch the network")

    dispatcher = DeliveryDispatcher(
        ClaimNextDelivery(
            factory,
            clock,
            RETRY,
            worker_id="worker",
            lease_duration=LEASE,
            candidate_limit=10,
        ),
        MarkDeliveryDispatchStarted(factory, clock),
        PersistDeliveryOutcome(factory, clock),
        factory,
        configured(route),
        UnreachableTransport(),
    )
    assert dispatcher.dispatch_once() is DispatchResult.AMBIGUOUS
    with factory() as uow:
        stored = uow.deliveries.get(delivery.id)
        assert stored is not None and stored.status is DeliveryStatus.AMBIGUOUS
        assert stored.failure_code == expected
        assert stored.dispatch_started_at is None
    engine.dispose()


def _attempts(factory: Callable[[], UnitOfWork], delivery_id: DeliveryId) -> list[DeliveryAttempt]:
    with factory() as uow:
        found = uow.delivery_attempts.list_for_delivery(delivery_id, 10)
        uow.commit()
    return found


def _dispatcher(
    factory: Callable[[], UnitOfWork],
    clock: FakeClock,
    routes: tuple[MessagingRoute, ...],
    transport: object,
) -> DeliveryDispatcher:
    return DeliveryDispatcher(
        ClaimNextDelivery(
            factory,
            clock,
            RETRY,
            worker_id="worker",
            lease_duration=LEASE,
            candidate_limit=10,
        ),
        BeginDeliveryAttempt(factory, clock),
        PersistDeliveryOutcome(factory, clock),
        factory,
        routes,
        cast(DeliveryTransport, transport),
    )


@pytest.mark.parametrize(
    ("result", "expected_dispatch", "expected_delivery_status", "expected_attempt_outcome"),
    [
        (
            TransportResult.delivered(),
            DispatchResult.DELIVERED,
            DeliveryStatus.DELIVERED,
            DeliveryAttemptOutcome.DELIVERED,
        ),
        (
            TransportResult.failed("webhook_http_4xx"),
            DispatchResult.FAILED,
            DeliveryStatus.FAILED,
            DeliveryAttemptOutcome.FAILED,
        ),
        (
            TransportResult.ambiguous("webhook_timeout"),
            DispatchResult.AMBIGUOUS,
            DeliveryStatus.AMBIGUOUS,
            DeliveryAttemptOutcome.AMBIGUOUS,
        ),
    ],
    ids=["delivered", "definite_failure", "ambiguous_transport"],
)
def test_sqlite_transport_outcome_closes_the_matching_attempt(
    tmp_path: Path,
    result: TransportResult,
    expected_dispatch: DispatchResult,
    expected_delivery_status: DeliveryStatus,
    expected_attempt_outcome: DeliveryAttemptOutcome,
) -> None:
    """Delivery status and its ledger row always land on the same verdict."""
    engine = create_engine(f"sqlite:///{tmp_path / 'outcome.db'}")
    Base.metadata.create_all(engine)
    factory = create_unit_of_work_factory(create_session_factory(engine))
    clock, route = FakeClock(T0), _route()
    delivery = _seed(factory, route)

    class StaticTransport:
        def send(self, request: TransportRequest) -> TransportResult:
            return result

    assert _dispatcher(factory, clock, (route,), StaticTransport()).dispatch_once() is (
        expected_dispatch
    )

    with factory() as uow:
        stored = uow.deliveries.get(delivery.id)
        assert stored is not None and stored.status is expected_delivery_status
        assert stored.dispatch_started_at is not None
        uow.commit()
    attempts = _attempts(factory, delivery.id)
    assert len(attempts) == 1
    assert attempts[0].outcome is expected_attempt_outcome
    assert attempts[0].finished_at is not None
    engine.dispose()


def test_sqlite_dispatcher_commits_the_attempt_before_transport(tmp_path: Path) -> None:
    """A separate session sees the IN_PROGRESS row while the send is in flight."""
    engine = create_engine(f"sqlite:///{tmp_path / 'ledger-before-send.db'}")
    Base.metadata.create_all(engine)
    factory = create_unit_of_work_factory(create_session_factory(engine))
    clock, route = FakeClock(T0), _route()
    delivery = _seed(factory, route)
    observed: list[DeliveryAttempt] = []

    class InspectingTransport:
        def send(self, request: TransportRequest) -> TransportResult:
            observed.extend(_attempts(factory, delivery.id))
            return TransportResult.delivered()

    assert (
        _dispatcher(factory, clock, (route,), InspectingTransport()).dispatch_once()
        is DispatchResult.DELIVERED
    )

    # In flight: exactly one attempt, still open, matching the boundary.
    assert len(observed) == 1
    assert observed[0].outcome is DeliveryAttemptOutcome.IN_PROGRESS
    assert observed[0].started_at == T0
    assert observed[0].finished_at is None
    engine.dispose()


def test_sqlite_transport_exception_is_post_dispatch_ambiguous_in_the_ledger(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'transport-exception.db'}")
    Base.metadata.create_all(engine)
    factory = create_unit_of_work_factory(create_session_factory(engine))
    clock, route = FakeClock(T0), _route()
    delivery = _seed(factory, route)

    class ExplodingTransport:
        def send(self, request: TransportRequest) -> TransportResult:
            raise RuntimeError(f"{route.endpoint} {delivery.body}")

    assert (
        _dispatcher(factory, clock, (route,), ExplodingTransport()).dispatch_once()
        is DispatchResult.AMBIGUOUS
    )

    with factory() as uow:
        stored = uow.deliveries.get(delivery.id)
        assert stored is not None and stored.status is DeliveryStatus.AMBIGUOUS
        assert stored.dispatch_started_at is not None
        uow.commit()
    attempts = _attempts(factory, delivery.id)
    assert len(attempts) == 1
    assert attempts[0].outcome is DeliveryAttemptOutcome.AMBIGUOUS
    # No exception text or endpoint reaches the ledger.
    assert attempts[0].failure_code == TRANSPORT_EXCEPTION
    engine.dispose()


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (lambda route: (), ROUTE_MISSING),
        (lambda route: (replace(route, enabled=False),), ROUTE_DISABLED),
        (
            lambda route: (replace(route, endpoint="https://hook.test/rotated"),),
            ROUTE_FINGERPRINT_MISMATCH,
        ),
    ],
    ids=["missing", "disabled", "fingerprint_mismatch"],
)
def test_sqlite_route_authority_drift_writes_zero_attempt_rows(
    tmp_path: Path,
    configured: Callable[[MessagingRoute], tuple[MessagingRoute, ...]],
    expected: str,
) -> None:
    """A pre-dispatch stop crossed no boundary, so it audits nothing."""
    engine = create_engine(f"sqlite:///{tmp_path / 'drift-ledger.db'}")
    Base.metadata.create_all(engine)
    factory = create_unit_of_work_factory(create_session_factory(engine))
    clock, route = FakeClock(T0), _route()
    delivery = _seed(factory, route)

    class UnreachableTransport:
        def send(self, request: TransportRequest) -> TransportResult:
            raise AssertionError("route authority drift must never touch the network")

    assert (
        _dispatcher(factory, clock, configured(route), UnreachableTransport()).dispatch_once()
        is DispatchResult.AMBIGUOUS
    )

    with factory() as uow:
        stored = uow.deliveries.get(delivery.id)
        assert stored is not None
        assert stored.status is DeliveryStatus.AMBIGUOUS
        assert stored.failure_code == expected
        assert stored.dispatch_started_at is None
        uow.commit()
    assert _attempts(factory, delivery.id) == []
    engine.dispose()


def test_sqlite_crash_after_marker_closes_the_same_attempt_as_ambiguous(
    tmp_path: Path,
) -> None:
    """End-to-end: crash mid-dispatch, then recovery closes the open attempt."""
    engine = create_engine(f"sqlite:///{tmp_path / 'crash-ledger.db'}")
    Base.metadata.create_all(engine)
    factory = create_unit_of_work_factory(create_session_factory(engine))
    clock, route = FakeClock(T0), _route()
    delivery = _seed(factory, route)
    claimer = ClaimNextDelivery(
        factory,
        clock,
        RETRY,
        worker_id="worker",
        lease_duration=LEASE,
        candidate_limit=10,
    )
    claim = claimer.execute()
    assert claim is not None
    boundary = BeginDeliveryAttempt(factory, clock).execute(claim)
    opened = _attempts(factory, delivery.id)
    assert len(opened) == 1
    assert opened[0].outcome is DeliveryAttemptOutcome.IN_PROGRESS

    # The worker dies here; the next tick recovers the expired lease.
    recovered_at = T0 + LEASE + timedelta(seconds=1)
    clock.fixed_now = recovered_at
    assert claimer.execute() is None

    with factory() as uow:
        stored = uow.deliveries.get(delivery.id)
        assert stored is not None and stored.status is DeliveryStatus.AMBIGUOUS
        assert stored.failure_code == LEASE_EXPIRED_AFTER_DISPATCH
        uow.commit()
    closed = _attempts(factory, delivery.id)
    assert len(closed) == 1
    assert closed[0].id == opened[0].id
    assert closed[0].started_at == boundary
    assert closed[0].outcome is DeliveryAttemptOutcome.AMBIGUOUS
    assert closed[0].failure_code == LEASE_EXPIRED_AFTER_DISPATCH
    assert closed[0].finished_at == recovered_at
    engine.dispose()


def test_sqlite_crash_after_marker_recovers_to_ambiguous(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'crash.db'}")
    Base.metadata.create_all(engine)
    factory = create_unit_of_work_factory(create_session_factory(engine))
    clock, route = FakeClock(T0), _route()
    delivery = _seed(factory, route)
    claimer = ClaimNextDelivery(
        factory,
        clock,
        RETRY,
        worker_id="worker",
        lease_duration=LEASE,
        candidate_limit=10,
    )
    claim = claimer.execute()
    assert claim is not None
    MarkDeliveryDispatchStarted(factory, clock).execute(claim)

    clock.fixed_now = T0 + LEASE + timedelta(seconds=1)
    assert claimer.execute() is None
    with factory() as uow:
        stored = uow.deliveries.get(delivery.id)
        assert stored is not None and stored.status is DeliveryStatus.AMBIGUOUS
    engine.dispose()
