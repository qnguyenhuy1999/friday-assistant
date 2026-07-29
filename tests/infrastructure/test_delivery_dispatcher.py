"""Outbound delivery dispatching is fenced, route-bound, and secret-safe."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import cast

import pytest

from friday.application.delivery_lifecycle import (
    ClaimNextDelivery,
    DeliveryClaim,
    MarkDeliveryDispatchStarted,
    PersistDeliveryOutcome,
)
from friday.application.errors import ClaimLost
from friday.application.retry_policy import RetryPolicy
from friday.domain.identifiers import DeliveryId, RunId, ToolInvocationId
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
from friday.infrastructure.messaging.transport_models import (
    TransportRequest,
    TransportResult,
)
from tests.application.fakes import T0, CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork

SECRET_ENDPOINT = "https://hook.example.test/a-secret-endpoint"
SECRET_BODY = "body containing a secret payload"
LEASE = timedelta(minutes=1)
RETRY = RetryPolicy(3, timedelta(seconds=1), 2.0, timedelta(seconds=30))


def _route(**overrides: object) -> MessagingRoute:
    values: dict[str, object] = {
        "route_id": "ops.alerts",
        "trusted_description": "Operations alerts",
        "enabled": True,
        "transport": "https_webhook",
        "principal_id": "ops",
        "endpoint_env": "MESSAGE_ENDPOINT",
        "endpoint": SECRET_ENDPOINT,
        "payload_field": "body",
        "max_body_chars": 1_000,
        "timeout_seconds": 10.0,
    }
    values.update(overrides)
    return MessagingRoute(**values)  # type: ignore[arg-type]


def _delivery(route: MessagingRoute) -> OutboundDelivery:
    return OutboundDelivery.new(
        id=DeliveryId.new(),
        source_kind=DeliverySourceKind.AGENT_REQUEST,
        source_run_id=RunId.new(),
        source_tool_invocation_id=ToolInvocationId.new(),
        route_id=route.route_id,
        route_fingerprint=route.fingerprint,
        subject=None,
        body=SECRET_BODY,
        available_at=T0,
        created_at=T0,
    )


@dataclass
class RecordingTransport:
    result: TransportResult = TransportResult.delivered()
    exception: Exception | None = None
    requests: list[TransportRequest] | None = None

    def send(self, request: TransportRequest) -> TransportResult:
        if self.requests is None:
            self.requests = []
        self.requests.append(request)
        if self.exception is not None:
            raise self.exception
        return self.result


def _dispatcher(
    uow: FakeUnitOfWork,
    clock: FakeClock,
    transport: RecordingTransport,
    routes: tuple[MessagingRoute, ...],
) -> DeliveryDispatcher:
    factory = CountingUnitOfWorkFactory(uow)
    return DeliveryDispatcher(
        claim_next=ClaimNextDelivery(
            factory,
            clock,
            RETRY,
            worker_id="delivery-worker",
            lease_duration=LEASE,
            candidate_limit=10,
        ),
        dispatch_started=MarkDeliveryDispatchStarted(factory, clock),
        persist_outcome=PersistDeliveryOutcome(factory, clock),
        uow_factory=factory,
        routes=routes,
        transport=transport,
    )


def test_successful_delivery_marks_boundary_before_transport_and_delivers() -> None:
    uow, clock, route, transport = FakeUnitOfWork(), FakeClock(T0), _route(), RecordingTransport()
    delivery = _delivery(route)
    uow.deliveries.add(delivery)
    dispatcher = _dispatcher(uow, clock, transport, (route,))

    assert dispatcher.dispatch_once() is DispatchResult.DELIVERED
    stored = uow.deliveries.get(delivery.id)
    assert stored is not None and stored.status is DeliveryStatus.DELIVERED
    assert stored.dispatch_started_at == T0
    assert transport.requests is not None and transport.requests[0].body == SECRET_BODY


@pytest.mark.parametrize("failure_code", ["webhook_http_4xx", "webhook_http_5xx"])
def test_response_failures_are_terminal_without_retries(failure_code: str) -> None:
    uow, clock, route = FakeUnitOfWork(), FakeClock(T0), _route()
    delivery = _delivery(route)
    uow.deliveries.add(delivery)
    dispatcher = _dispatcher(
        uow, clock, RecordingTransport(TransportResult.failed(failure_code)), (route,)
    )

    assert dispatcher.dispatch_once() is DispatchResult.FAILED
    stored = uow.deliveries.get(delivery.id)
    assert stored is not None and stored.status is DeliveryStatus.FAILED
    assert stored.failure_code == failure_code and stored.dispatch_started_at == T0


@pytest.mark.parametrize("failure_code", ["webhook_timeout", "webhook_connection_error"])
def test_no_response_transport_failures_are_ambiguous(failure_code: str) -> None:
    uow, clock, route = FakeUnitOfWork(), FakeClock(T0), _route()
    delivery = _delivery(route)
    uow.deliveries.add(delivery)
    dispatcher = _dispatcher(
        uow,
        clock,
        RecordingTransport(TransportResult.ambiguous(failure_code)),
        (route,),
    )

    assert dispatcher.dispatch_once() is DispatchResult.AMBIGUOUS
    stored = uow.deliveries.get(delivery.id)
    assert stored is not None and stored.status is DeliveryStatus.AMBIGUOUS
    assert stored.failure_code == failure_code and stored.dispatch_started_at == T0


def test_fingerprint_mismatch_is_ambiguous_without_network_io() -> None:
    uow, clock, route, transport = FakeUnitOfWork(), FakeClock(T0), _route(), RecordingTransport()
    delivery = _delivery(route)
    uow.deliveries.add(delivery)
    changed = _route(endpoint="https://hook.example.test/changed-secret")

    result = _dispatcher(uow, clock, transport, (changed,)).dispatch_once()
    assert result is DispatchResult.AMBIGUOUS
    stored = uow.deliveries.get(delivery.id)
    assert stored is not None and stored.status is DeliveryStatus.AMBIGUOUS
    assert stored.failure_code == ROUTE_FINGERPRINT_MISMATCH
    assert stored.dispatch_started_at is None and transport.requests is None


@pytest.mark.parametrize(
    ("routes", "expected"),
    [((), ROUTE_MISSING), ((_route(enabled=False),), ROUTE_DISABLED)],
)
def test_missing_or_disabled_route_fails_without_network_io(
    routes: tuple[MessagingRoute, ...], expected: str
) -> None:
    uow, clock, route, transport = FakeUnitOfWork(), FakeClock(T0), _route(), RecordingTransport()
    delivery = _delivery(route)
    uow.deliveries.add(delivery)

    assert _dispatcher(uow, clock, transport, routes).dispatch_once() is DispatchResult.FAILED
    stored = uow.deliveries.get(delivery.id)
    assert stored is not None and stored.failure_code == expected
    assert stored.dispatch_started_at is None and transport.requests is None


def test_already_dispatched_delivery_is_never_sent() -> None:
    uow, clock, route, transport = FakeUnitOfWork(), FakeClock(T0), _route(), RecordingTransport()
    delivery = _delivery(route)
    uow.deliveries.add(delivery)
    factory = CountingUnitOfWorkFactory(uow)
    claim = ClaimNextDelivery(
        factory,
        clock,
        RETRY,
        worker_id="delivery-worker",
        lease_duration=LEASE,
        candidate_limit=10,
    ).execute()
    assert claim is not None
    MarkDeliveryDispatchStarted(factory, clock).execute(claim)

    class StaticClaim:
        def execute(self) -> DeliveryClaim:
            return cast(DeliveryClaim, claim)

    dispatcher = DeliveryDispatcher(
        StaticClaim(),
        MarkDeliveryDispatchStarted(factory, clock),
        PersistDeliveryOutcome(factory, clock),
        factory,
        (route,),
        transport,
    )
    assert dispatcher.dispatch_once() is DispatchResult.AMBIGUOUS
    assert transport.requests is None


def test_transport_exception_is_ambiguous_without_exception_text_leakage() -> None:
    uow, clock, route = FakeUnitOfWork(), FakeClock(T0), _route()
    delivery = _delivery(route)
    uow.deliveries.add(delivery)
    dispatcher = _dispatcher(
        uow,
        clock,
        RecordingTransport(exception=RuntimeError(f"{SECRET_ENDPOINT} {SECRET_BODY}")),
        (route,),
    )

    assert dispatcher.dispatch_once() is DispatchResult.AMBIGUOUS
    stored = uow.deliveries.get(delivery.id)
    assert stored is not None and stored.status is DeliveryStatus.AMBIGUOUS
    assert stored.failure_code == TRANSPORT_EXCEPTION
    assert SECRET_ENDPOINT not in (stored.failure_message or "")
    assert SECRET_BODY not in (stored.failure_message or "")


def test_lost_outcome_claim_is_contained() -> None:
    uow, clock, route = FakeUnitOfWork(), FakeClock(T0), _route()
    delivery = _delivery(route)
    uow.deliveries.add(delivery)

    class LostOutcome:
        def deliver(self, _: DeliveryClaim) -> None:
            raise ClaimLost("outcome fenced")

    factory = CountingUnitOfWorkFactory(uow)
    dispatcher = DeliveryDispatcher(
        ClaimNextDelivery(
            factory,
            clock,
            RETRY,
            worker_id="delivery-worker",
            lease_duration=LEASE,
            candidate_limit=10,
        ),
        MarkDeliveryDispatchStarted(factory, clock),
        cast(PersistDeliveryOutcome, LostOutcome()),
        factory,
        (route,),
        RecordingTransport(),
    )

    assert dispatcher.dispatch_once() is DispatchResult.CLAIM_LOST
    stored = uow.deliveries.get(delivery.id)
    assert stored is not None and stored.status is DeliveryStatus.SENDING


def test_secret_values_are_not_exposed_by_transport_models_or_routes() -> None:
    route = _route()
    request = TransportRequest(route, SECRET_BODY)
    assert SECRET_ENDPOINT not in repr(route)
    assert SECRET_ENDPOINT not in repr(request)
    assert SECRET_BODY not in repr(request)
