"""SQLite integration tests for the dispatcher transaction boundaries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from friday.application.delivery_lifecycle import (
    ClaimNextDelivery,
    MarkDeliveryDispatchStarted,
    PersistDeliveryOutcome,
)
from friday.application.ports import UnitOfWork
from friday.application.retry_policy import RetryPolicy
from friday.domain import Run, Task, ToolInvocation
from friday.domain.identifiers import DeliveryId, RunId, TaskId, ToolInvocationId
from friday.domain.outbound_delivery import DeliverySourceKind, DeliveryStatus, OutboundDelivery
from friday.infrastructure.messaging.config import MessagingRoute
from friday.infrastructure.messaging.dispatcher import (
    ROUTE_DISABLED,
    ROUTE_FINGERPRINT_MISMATCH,
    ROUTE_MISSING,
    DeliveryDispatcher,
    DispatchResult,
)
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
