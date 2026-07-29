"""Delivery claim fencing, the dispatch boundary, and expired-lease recovery."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from friday.application.delivery_lifecycle import (
    LEASE_EXPIRED_AFTER_DISPATCH,
    PRE_DISPATCH_ATTEMPTS_EXHAUSTED,
    ClaimNextDelivery,
    DeliveryClaim,
    MarkDeliveryDispatchStarted,
    PersistDeliveryOutcome,
    RecoverExpiredDeliveryClaims,
    VerifyDeliveryClaim,
)
from friday.application.errors import ClaimLost
from friday.application.retry_policy import RetryPolicy
from friday.domain.identifiers import DeliveryId, RunId, ToolInvocationId
from friday.domain.outbound_delivery import DeliverySourceKind, DeliveryStatus, OutboundDelivery
from tests.application.fakes import T0, CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork

FINGERPRINT = "a" * 64
LEASE = timedelta(minutes=1)
RETRY_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=timedelta(seconds=30),
    multiplier=2.0,
    max_delay=timedelta(minutes=5),
)


def _delivery(available_at: datetime = T0) -> OutboundDelivery:
    return OutboundDelivery.new(
        id=DeliveryId.new(),
        source_kind=DeliverySourceKind.AGENT_REQUEST,
        source_run_id=RunId.new(),
        source_tool_invocation_id=ToolInvocationId.new(),
        route_id="personal.notifications",
        route_fingerprint=FINGERPRINT,
        subject="subject",
        body="hello",
        available_at=available_at,
        created_at=T0,
    )


def _claimer(
    uow_factory: CountingUnitOfWorkFactory,
    clock: FakeClock,
    *,
    worker_id: str = "worker-a",
    candidate_limit: int = 5,
) -> ClaimNextDelivery:
    return ClaimNextDelivery(
        uow_factory,
        clock,
        RETRY_POLICY,
        worker_id=worker_id,
        lease_duration=LEASE,
        candidate_limit=candidate_limit,
    )


def _stored(uow: FakeUnitOfWork, delivery_id: DeliveryId) -> OutboundDelivery:
    stored = uow.deliveries.get(delivery_id)
    assert stored is not None
    return stored


def test_claim_next_delivery_returns_one_claim_and_commits(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    delivery = _delivery()
    fake_uow.deliveries.add(delivery)

    claim = _claimer(uow_factory, clock).execute()

    assert claim is not None
    assert claim.delivery_id == delivery.id
    assert claim.worker_id == "worker-a"
    assert claim.claim_generation == 1
    assert claim.acquired_at == T0
    assert claim.lease_expires_at == T0 + LEASE
    assert fake_uow.commit_count == 1

    stored = _stored(fake_uow, delivery.id)
    assert stored.status is DeliveryStatus.SENDING
    assert stored.attempt_count == 1
    assert stored.dispatch_started_at is None


def test_claim_next_delivery_returns_none_when_nothing_is_claimable(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    fake_uow.deliveries.add(_delivery(available_at=T0 + timedelta(minutes=10)))
    assert _claimer(uow_factory, clock).execute() is None
    assert fake_uow.commit_count == 1


def test_claim_next_delivery_skips_a_lost_race_and_takes_the_next_candidate(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    first, second = _delivery(), _delivery()
    fake_uow.deliveries.add(first)
    fake_uow.deliveries.add(second)
    contended = min(first.id, second.id, key=str)
    other = second.id if contended == first.id else first.id

    real_try_claim = fake_uow.deliveries.try_claim
    losses: list[DeliveryId] = []

    def try_claim(  # noqa: PLR0913 — mirrors the port signature exactly
        delivery_id: DeliveryId,
        worker_id: str,
        claim_token: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> int | None:
        if delivery_id == contended:
            losses.append(delivery_id)
            return None
        return real_try_claim(delivery_id, worker_id, claim_token, now, lease_expires_at)

    fake_uow.deliveries.try_claim = try_claim  # type: ignore[method-assign]

    claim = _claimer(uow_factory, clock).execute()
    assert claim is not None
    assert claim.delivery_id == other
    assert losses == [contended]


def test_verify_delivery_claim_fails_closed_for_stale_token_generation_and_expiry(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    delivery = _delivery()
    fake_uow.deliveries.add(delivery)
    claim = _claimer(uow_factory, clock).execute()
    assert claim is not None

    verifier = VerifyDeliveryClaim(uow_factory, clock)
    assert verifier.execute(claim) is True

    from dataclasses import replace

    assert verifier.execute(replace(claim, worker_id="worker-b")) is False
    assert verifier.execute(replace(claim, claim_token="other-token")) is False
    assert verifier.execute(replace(claim, claim_generation=claim.claim_generation + 1)) is False

    # Equality is expired: the lease must be strictly in the future.
    clock.fixed_now = claim.lease_expires_at
    assert verifier.execute(claim) is False


def test_mark_dispatch_started_is_idempotent_once_and_fences_stale_workers(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    delivery = _delivery()
    fake_uow.deliveries.add(delivery)
    claim = _claimer(uow_factory, clock).execute()
    assert claim is not None

    marker = MarkDeliveryDispatchStarted(uow_factory, clock)
    clock.fixed_now = T0 + timedelta(seconds=5)
    boundary = marker.execute(claim)
    assert boundary == T0 + timedelta(seconds=5)
    assert _stored(fake_uow, delivery.id).dispatch_started_at == boundary

    # The boundary is crossed exactly once, and never by a stale claim.
    with pytest.raises(ClaimLost):
        marker.execute(claim)
    assert _stored(fake_uow, delivery.id).dispatch_started_at == boundary


def test_mark_dispatch_started_rejects_a_stale_generation(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    delivery = _delivery()
    fake_uow.deliveries.add(delivery)
    claim = _claimer(uow_factory, clock).execute()
    assert claim is not None
    from dataclasses import replace

    with pytest.raises(ClaimLost):
        MarkDeliveryDispatchStarted(uow_factory, clock).execute(replace(claim, claim_generation=99))
    assert _stored(fake_uow, delivery.id).dispatch_started_at is None


def test_persist_outcome_requires_the_dispatch_boundary_then_records_delivered(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    delivery = _delivery()
    fake_uow.deliveries.add(delivery)
    claim = _claimer(uow_factory, clock).execute()
    assert claim is not None
    outcomes = PersistDeliveryOutcome(uow_factory, clock)

    from friday.domain.errors import InvalidStateTransition

    with pytest.raises(InvalidStateTransition):
        outcomes.deliver(claim)
    assert _stored(fake_uow, delivery.id).status is DeliveryStatus.SENDING

    MarkDeliveryDispatchStarted(uow_factory, clock).execute(claim)
    outcomes.deliver(claim, provider_message_id="provider-1")
    stored = _stored(fake_uow, delivery.id)
    assert stored.status is DeliveryStatus.DELIVERED
    assert stored.provider_message_id == "provider-1"


def test_persist_outcome_can_release_a_pre_dispatch_delivery_for_retry(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    delivery = _delivery()
    fake_uow.deliveries.add(delivery)
    claim = _claimer(uow_factory, clock).execute()
    assert claim is not None

    retry_at = T0 + timedelta(minutes=2)
    PersistDeliveryOutcome(uow_factory, clock).release_for_retry(claim, available_at=retry_at)
    stored = _stored(fake_uow, delivery.id)
    assert stored.status is DeliveryStatus.QUEUED
    assert stored.available_at == retry_at
    assert stored.claim_owner is None
    assert (stored.attempt_count, stored.claim_generation) == (1, 1)


def test_persist_outcome_never_rewrites_authority_or_content(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    delivery = _delivery()
    fake_uow.deliveries.add(delivery)
    claim = _claimer(uow_factory, clock).execute()
    assert claim is not None
    MarkDeliveryDispatchStarted(uow_factory, clock).execute(claim)
    PersistDeliveryOutcome(uow_factory, clock).mark_ambiguous(
        claim, failure_code="timeout", failure_message="no response"
    )

    stored = _stored(fake_uow, delivery.id)
    assert stored.status is DeliveryStatus.AMBIGUOUS
    assert stored.route_id == delivery.route_id
    assert stored.route_fingerprint == delivery.route_fingerprint
    assert stored.subject == delivery.subject
    assert stored.body == delivery.body
    assert stored.body_sha256 == delivery.body_sha256
    assert stored.source_run_id == delivery.source_run_id
    assert stored.source_tool_invocation_id == delivery.source_tool_invocation_id


def test_persist_outcome_fences_a_stale_worker(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    delivery = _delivery()
    fake_uow.deliveries.add(delivery)
    claim = _claimer(uow_factory, clock).execute()
    assert claim is not None
    from dataclasses import replace

    outcomes = PersistDeliveryOutcome(uow_factory, clock)
    for stale in (
        replace(claim, worker_id="worker-b"),
        replace(claim, claim_token="other"),
        replace(claim, claim_generation=claim.claim_generation + 1),
    ):
        with pytest.raises(ClaimLost):
            outcomes.fail(stale, failure_code="c", failure_message="m")
    assert _stored(fake_uow, delivery.id).status is DeliveryStatus.SENDING


def test_persist_outcome_fences_an_expired_lease(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    delivery = _delivery()
    fake_uow.deliveries.add(delivery)
    claim = _claimer(uow_factory, clock).execute()
    assert claim is not None

    clock.fixed_now = claim.lease_expires_at + timedelta(seconds=1)
    with pytest.raises(ClaimLost):
        PersistDeliveryOutcome(uow_factory, clock).fail(
            claim, failure_code="c", failure_message="m"
        )
    assert _stored(fake_uow, delivery.id).status is DeliveryStatus.SENDING


def test_recovery_requeues_an_expired_pre_dispatch_claim_with_bounded_backoff(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    delivery = _delivery()
    fake_uow.deliveries.add(delivery)
    claim = _claimer(uow_factory, clock).execute()
    assert claim is not None

    recovered_at = claim.lease_expires_at + timedelta(seconds=1)
    recovery = RecoverExpiredDeliveryClaims(RETRY_POLICY, candidate_limit=10)
    assert recovery.execute(fake_uow, recovered_at) == 1

    stored = _stored(fake_uow, delivery.id)
    assert stored.status is DeliveryStatus.QUEUED
    # attempt 1 expired, so the retry is attempt 2 => exactly base_delay.
    assert stored.available_at == recovered_at + RETRY_POLICY.compute_delay(2)
    assert (stored.attempt_count, stored.claim_generation) == (1, 1)
    assert stored.claim_owner is None and stored.claim_token is None
    assert stored.claim_expires_at is None
    assert stored.failure_code is None


def test_recovery_parks_an_expired_post_dispatch_claim_as_ambiguous(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    delivery = _delivery()
    fake_uow.deliveries.add(delivery)
    claim = _claimer(uow_factory, clock).execute()
    assert claim is not None
    MarkDeliveryDispatchStarted(uow_factory, clock).execute(claim)

    recovered_at = claim.lease_expires_at + timedelta(seconds=1)
    assert (
        RecoverExpiredDeliveryClaims(RETRY_POLICY, candidate_limit=10).execute(
            fake_uow, recovered_at
        )
        == 1
    )

    stored = _stored(fake_uow, delivery.id)
    assert stored.status is DeliveryStatus.AMBIGUOUS
    assert stored.failure_code == LEASE_EXPIRED_AFTER_DISPATCH
    assert stored.dispatch_started_at is not None
    # Never automatically resent.
    assert fake_uow.deliveries.list_due(recovered_at + timedelta(days=1), 10) == []


def test_recovery_respects_max_attempts_and_ends_in_failed(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    delivery = _delivery()
    fake_uow.deliveries.add(delivery)
    claimer = _claimer(uow_factory, clock)
    now = T0

    for attempt in range(1, RETRY_POLICY.max_attempts + 1):
        clock.fixed_now = now
        claim = claimer.execute()
        assert claim is not None, f"attempt {attempt} should have been claimable"
        assert claim.claim_generation == attempt
        assert _stored(fake_uow, delivery.id).attempt_count == attempt

        # The lease expires with the boundary uncrossed: nothing was ever sent,
        # so this tick recovers the delivery to QUEUED behind its backoff.
        clock.fixed_now = claim.lease_expires_at + timedelta(seconds=1)
        assert claimer.execute() is None, "backoff must not be claimable immediately"
        requeued = _stored(fake_uow, delivery.id)
        if attempt < RETRY_POLICY.max_attempts:
            assert requeued.status is DeliveryStatus.QUEUED
            now = requeued.available_at
        else:
            assert requeued.status is DeliveryStatus.FAILED

    clock.fixed_now = now + timedelta(days=1)
    assert claimer.execute() is None
    stored = _stored(fake_uow, delivery.id)
    assert stored.status is DeliveryStatus.FAILED
    assert stored.failure_code == PRE_DISPATCH_ATTEMPTS_EXHAUSTED
    assert stored.attempt_count == RETRY_POLICY.max_attempts
    assert stored.dispatch_started_at is None
    assert "external message" in (stored.failure_message or "")


def test_recovery_backoff_is_bounded_by_max_delay() -> None:
    tight = RetryPolicy(
        max_attempts=9,
        base_delay=timedelta(seconds=30),
        multiplier=10.0,
        max_delay=timedelta(minutes=5),
    )
    uow = FakeUnitOfWork()
    delivery = _delivery()
    uow.delivery_repo.add(delivery)
    claim_generation = uow.delivery_repo.try_claim(delivery.id, "w", "t", T0, T0 + LEASE)
    assert claim_generation == 1
    # Force a high attempt_count so unbounded backoff would exceed max_delay.
    uow.delivery_repo.items[delivery.id].attempt_count = 6

    recovered_at = T0 + LEASE + timedelta(seconds=1)
    assert RecoverExpiredDeliveryClaims(tight, candidate_limit=10).execute(uow, recovered_at) == 1
    assert uow.delivery_repo.items[delivery.id].available_at == recovered_at + tight.max_delay


def test_claiming_recovers_before_selecting_candidates(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    """One short transaction recovers expired leases and then claims."""
    delivery = _delivery()
    fake_uow.deliveries.add(delivery)
    claimer = _claimer(uow_factory, clock)
    first = claimer.execute()
    assert first is not None

    # This tick only recovers: the requeued delivery sits behind its backoff.
    clock.fixed_now = first.lease_expires_at + timedelta(seconds=1)
    assert claimer.execute() is None
    requeued = _stored(fake_uow, delivery.id)
    assert requeued.status is DeliveryStatus.QUEUED

    # Once the backoff elapses the same delivery is claimable under a new
    # generation, which fences the original worker out.
    clock.fixed_now = requeued.available_at
    second = claimer.execute()
    assert second is not None
    assert second.claim_generation == 2
    assert second.claim_token != first.claim_token
    assert _stored(fake_uow, delivery.id).attempt_count == 2


def test_claim_construction_rejects_degenerate_configuration(
    uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    with pytest.raises(ValueError, match="lease_duration"):
        ClaimNextDelivery(
            uow_factory,
            clock,
            RETRY_POLICY,
            worker_id="w",
            lease_duration=timedelta(0),
            candidate_limit=1,
        )
    with pytest.raises(ValueError, match="candidate_limit"):
        ClaimNextDelivery(
            uow_factory,
            clock,
            RETRY_POLICY,
            worker_id="w",
            lease_duration=LEASE,
            candidate_limit=0,
        )


def test_delivery_claim_is_immutable() -> None:
    claim = DeliveryClaim(
        delivery_id=DeliveryId.new(),
        worker_id="worker-a",
        claim_token="token",
        claim_generation=1,
        acquired_at=T0,
        lease_expires_at=T0 + LEASE,
    )
    with pytest.raises(AttributeError):
        claim.claim_generation = 2  # type: ignore[misc]
