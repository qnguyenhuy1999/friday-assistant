from __future__ import annotations

from datetime import timedelta

import pytest

from friday.application.delivery_lifecycle import (
    ClaimNextDelivery,
    PersistDeliveryOutcome,
    VerifyDeliveryClaim,
)
from friday.application.errors import ClaimLost
from friday.domain.identifiers import DeliveryId, RunId, ToolInvocationId
from friday.domain.outbound_delivery import DeliverySourceKind, DeliveryStatus, OutboundDelivery
from tests.application.fakes import CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork


def _queued(clock: FakeClock) -> OutboundDelivery:
    return OutboundDelivery.new(
        id=DeliveryId.new(),
        source_kind=DeliverySourceKind.AGENT_REQUEST,
        source_run_id=RunId.new(),
        source_tool_invocation_id=ToolInvocationId.new(),
        route_id="personal.notifications",
        route_fingerprint="a" * 64,
        body="hello",
        available_at=clock.now(),
        created_at=clock.now(),
    )


def test_two_workers_claim_one_delivery_once() -> None:
    clock = FakeClock()
    uow = FakeUnitOfWork()
    uow.deliveries.add(_queued(clock))
    factory = CountingUnitOfWorkFactory(uow)

    first = ClaimNextDelivery(
        factory, clock, worker_id="a", lease_duration=timedelta(minutes=1), candidate_limit=10
    ).execute()
    second = ClaimNextDelivery(
        factory, clock, worker_id="b", lease_duration=timedelta(minutes=1), candidate_limit=10
    ).execute()

    assert first is not None
    assert second is None
    assert uow.deliveries.get(first.delivery_id).status is DeliveryStatus.SENDING  # type: ignore[union-attr]


def test_expired_in_flight_delivery_becomes_ambiguous_not_requeued() -> None:
    clock = FakeClock()
    uow = FakeUnitOfWork()
    delivery = _queued(clock)
    uow.deliveries.add(delivery)
    factory = CountingUnitOfWorkFactory(uow)
    claim = ClaimNextDelivery(
        factory, clock, worker_id="a", lease_duration=timedelta(seconds=1), candidate_limit=10
    ).execute()
    assert claim is not None

    clock.fixed_now += timedelta(seconds=1)
    assert (
        ClaimNextDelivery(
            factory, clock, worker_id="b", lease_duration=timedelta(minutes=1), candidate_limit=10
        ).execute()
        is None
    )
    recovered = uow.deliveries.get(delivery.id)
    assert recovered is not None
    assert recovered.status is DeliveryStatus.AMBIGUOUS
    assert recovered.failure_code == "delivery_lease_expired"


def test_stale_worker_cannot_persist_delivery_outcome() -> None:
    clock = FakeClock()
    uow = FakeUnitOfWork()
    delivery = _queued(clock)
    uow.deliveries.add(delivery)
    factory = CountingUnitOfWorkFactory(uow)
    claim = ClaimNextDelivery(
        factory, clock, worker_id="a", lease_duration=timedelta(seconds=1), candidate_limit=10
    ).execute()
    assert claim is not None
    outcome = uow.deliveries.get(delivery.id)
    assert outcome is not None
    outcome.deliver(at=clock.now())
    clock.fixed_now += timedelta(seconds=1)

    with pytest.raises(ClaimLost):
        PersistDeliveryOutcome(factory, clock).execute(claim, outcome)

    persisted = uow.deliveries.get(delivery.id)
    assert persisted is not None
    assert persisted.status is DeliveryStatus.SENDING
    assert VerifyDeliveryClaim(factory, clock).execute(claim) is False
