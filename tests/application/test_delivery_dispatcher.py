from __future__ import annotations

from datetime import timedelta

from friday.application.delivery_dispatcher import (
    DeliveryDispatcher,
    TransportOutcome,
    TransportOutcomeKind,
)
from friday.application.delivery_dispatcher import (
    MessagingRoute as DeliveryMessagingRoute,
)
from friday.application.delivery_lifecycle import (
    ClaimNextDelivery,
    PersistDeliveryOutcome,
    VerifyDeliveryClaim,
)
from friday.application.retry_policy import RetryPolicy
from friday.domain.identifiers import DeliveryId, RunId, ToolInvocationId
from friday.domain.outbound_delivery import DeliverySourceKind, DeliveryStatus, OutboundDelivery
from friday.infrastructure.messaging.config import MessagingRoute, MessagingRoutes
from tests.application.fakes import CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork


class _Transport:
    def __init__(self, outcome: TransportOutcome) -> None:
        self.outcome = outcome
        self.calls = 0

    def deliver(
        self, route: DeliveryMessagingRoute, delivery: OutboundDelivery
    ) -> TransportOutcome:
        del route, delivery
        self.calls += 1
        return self.outcome


def _setup(outcome: TransportOutcome, *, changed_route: bool = False):  # type: ignore[no-untyped-def]
    clock = FakeClock()
    uow = FakeUnitOfWork()
    endpoint = "https://example.test/a"
    route = MessagingRoute(
        route_id="personal.notifications",
        trusted_description="Personal notifications",
        principal_id="personal",
        endpoint=endpoint,
    )
    delivery = OutboundDelivery.new(
        id=DeliveryId.new(),
        source_kind=DeliverySourceKind.AGENT_REQUEST,
        source_run_id=RunId.new(),
        source_tool_invocation_id=ToolInvocationId.new(),
        route_id=route.route_id,
        route_fingerprint=("b" * 64 if changed_route else route.fingerprint),
        body="message",
        available_at=clock.now(),
        created_at=clock.now(),
    )
    uow.delivery_repo.add(delivery)
    factory = CountingUnitOfWorkFactory(uow)
    transport = _Transport(outcome)
    dispatcher = DeliveryDispatcher(
        claim_next=ClaimNextDelivery(
            factory,
            clock,
            worker_id="delivery-worker",
            lease_duration=timedelta(minutes=1),
            candidate_limit=10,
        ),
        verify_claim=VerifyDeliveryClaim(factory, clock),
        persist_outcome=PersistDeliveryOutcome(factory, clock),
        uow_factory=factory,
        clock=clock,
        routes=MessagingRoutes((route,)),
        transport=transport,
        retry_policy=RetryPolicy(
            max_attempts=3,
            base_delay=timedelta(seconds=5),
            multiplier=2,
            max_delay=timedelta(minutes=5),
        ),
    )
    return dispatcher, transport, uow, delivery, clock


def test_dispatcher_delivers_once_after_claim_fences() -> None:
    dispatcher, transport, uow, delivery, _ = _setup(
        TransportOutcome(TransportOutcomeKind.DELIVERED)
    )

    assert dispatcher.dispatch_once() is True
    assert transport.calls == 1
    assert uow.delivery_repo.get(delivery.id).status is DeliveryStatus.DELIVERED


def test_dispatcher_never_retargets_changed_route() -> None:
    dispatcher, transport, uow, delivery, _ = _setup(
        TransportOutcome(TransportOutcomeKind.DELIVERED), changed_route=True
    )

    assert dispatcher.dispatch_once() is True
    persisted = uow.delivery_repo.get(delivery.id)
    assert persisted is not None
    assert transport.calls == 0
    assert persisted.status is DeliveryStatus.FAILED
    assert persisted.failure_code == "route_binding_changed"


def test_definite_pre_dispatch_failure_requeues_with_backoff() -> None:
    dispatcher, transport, uow, delivery, clock = _setup(
        TransportOutcome(
            TransportOutcomeKind.PRE_DISPATCH_FAILURE,
            failure_code="socket_unavailable_before_dispatch",
            failure_message="socket could not be created",
        )
    )

    assert dispatcher.dispatch_once() is True
    persisted = uow.delivery_repo.get(delivery.id)
    assert persisted is not None
    assert transport.calls == 1
    assert persisted.status is DeliveryStatus.QUEUED
    assert persisted.available_at == clock.now() + timedelta(seconds=5)


def test_uncertain_transport_outcome_is_terminal_ambiguous() -> None:
    dispatcher, transport, uow, delivery, _ = _setup(
        TransportOutcome(
            TransportOutcomeKind.AMBIGUOUS,
            failure_code="webhook_transport_uncertain",
            failure_message="response lost",
        )
    )

    assert dispatcher.dispatch_once() is True
    persisted = uow.delivery_repo.get(delivery.id)
    assert persisted is not None
    assert transport.calls == 1
    assert persisted.status is DeliveryStatus.AMBIGUOUS


def test_transport_error_text_is_never_persisted() -> None:
    dispatcher, _, uow, delivery, _ = _setup(
        TransportOutcome(
            TransportOutcomeKind.AMBIGUOUS,
            failure_code="webhook_transport_uncertain",
            failure_message="remote response contained credential=fixture-secret",
        )
    )

    assert dispatcher.dispatch_once() is True
    persisted = uow.delivery_repo.get(delivery.id)
    assert persisted is not None
    assert persisted.failure_code == "webhook_transport_uncertain"
    assert "fixture-secret" not in repr(persisted)
