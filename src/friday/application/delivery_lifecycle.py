"""Durable claim/lease fencing and recovery for outbound deliveries.

No transport lives here. These use cases only make a delivery's ownership
and its external side-effect boundary durable, so that a later dispatcher can
send at most one external message per delivery and a crash is always
recoverable into an honest state.

The invariant every operation exists to protect:

    QUEUED
      -> claimed SENDING, dispatch_started_at = NULL
      -> dispatch boundary, dispatch_started_at = <time>
      -> (future) external I/O

An expired lease is therefore not one situation but two. Before the boundary,
no external message can exist, so a bounded retry is safe. After it, a message
may exist, so the delivery becomes AMBIGUOUS and is never resent
automatically. Recovery reads `dispatch_started_at` to tell the cases apart —
that is the whole reason the column is durable rather than in-memory.

Every mutation is fenced on (worker_id, claim_token, claim_generation) and an
unexpired lease. A worker whose lease expired and was recovered is fenced out
by the generation bump: its writes match nothing and raise `ClaimLost`. No
transaction here spans external I/O.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from friday.application.errors import ClaimLost
from friday.application.ports import Clock, UnitOfWork, UnitOfWorkFactory
from friday.application.retry_policy import RetryPolicy
from friday.domain.identifiers import DeliveryId
from friday.domain.outbound_delivery import DeliveryStatus, OutboundDelivery

#: Friday-owned failure codes. Recovery never persists untrusted external
#: error text; these are stable, greppable, and safe to surface.
PRE_DISPATCH_ATTEMPTS_EXHAUSTED = "delivery_pre_dispatch_attempts_exhausted"
LEASE_EXPIRED_AFTER_DISPATCH = "delivery_lease_expired_after_dispatch"

_PRE_DISPATCH_EXHAUSTED_MESSAGE = (
    "Delivery lease expired before dispatch and the retry budget is exhausted; "
    "no external message was sent."
)
_POST_DISPATCH_AMBIGUOUS_MESSAGE = (
    "Delivery lease expired after the dispatch boundary was crossed; "
    "the external message may or may not have been sent."
)

#: A domain lifecycle transition applied to a claimed delivery.
_Transition = Callable[[OutboundDelivery, datetime], None]


@dataclass(frozen=True, slots=True)
class DeliveryClaim:
    """Proof of exclusive, time-bounded ownership of one delivery."""

    delivery_id: DeliveryId
    worker_id: str
    claim_token: str
    claim_generation: int
    acquired_at: datetime
    lease_expires_at: datetime


class RecoverExpiredDeliveryClaims:
    """Resolve every expired delivery lease into an honest durable state.

    Runs inside a caller-provided UnitOfWork so claiming can recover first
    and then claim within one short transaction.
    """

    def __init__(self, retry_policy: RetryPolicy, *, candidate_limit: int) -> None:
        self._retry_policy = retry_policy
        self._candidate_limit = candidate_limit

    def execute(self, uow: UnitOfWork, now: datetime) -> int:
        expired = uow.deliveries.find_expired_claims(now, self._candidate_limit)
        recovered = 0
        for delivery in expired:
            if self._recover_one(uow, delivery, now):
                recovered += 1
        return recovered

    def _recover_one(self, uow: UnitOfWork, delivery: OutboundDelivery, now: datetime) -> bool:
        if delivery.dispatch_started_at is not None:
            # Case B: the side effect may already have happened.
            return uow.deliveries.mark_expired_post_dispatch_ambiguous(
                delivery.id,
                delivery.claim_generation,
                now,
                LEASE_EXPIRED_AFTER_DISPATCH,
                _POST_DISPATCH_AMBIGUOUS_MESSAGE,
            )
        # Case A: definitely nothing was sent, so a bounded retry is safe.
        if delivery.attempt_count < self._retry_policy.max_attempts:
            next_attempt = delivery.attempt_count + 1
            available_at = now + self._retry_policy.compute_delay(next_attempt)
            return uow.deliveries.requeue_expired_pre_dispatch(
                delivery.id, delivery.claim_generation, now, available_at
            )
        return uow.deliveries.fail_expired_pre_dispatch(
            delivery.id,
            delivery.claim_generation,
            now,
            PRE_DISPATCH_ATTEMPTS_EXHAUSTED,
            _PRE_DISPATCH_EXHAUSTED_MESSAGE,
        )


class ClaimNextDelivery:
    """Recover expired leases, then atomically claim at most one delivery.

    The transaction is deliberately short and closes before the caller does
    anything external: it must never wrap future dispatch I/O.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        retry_policy: RetryPolicy,
        *,
        worker_id: str,
        lease_duration: timedelta,
        candidate_limit: int,
    ) -> None:
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be greater than zero")
        if candidate_limit < 1:
            raise ValueError("candidate_limit must be at least 1")
        self._uow_factory = uow_factory
        self._clock = clock
        self._worker_id = worker_id
        self._lease_duration = lease_duration
        self._candidate_limit = candidate_limit
        self._recovery = RecoverExpiredDeliveryClaims(retry_policy, candidate_limit=candidate_limit)

    def execute(self) -> DeliveryClaim | None:
        with self._uow_factory() as uow:
            now = self._clock.now()
            self._recovery.execute(uow, now)
            lease_expires_at = now + self._lease_duration
            for candidate in uow.deliveries.list_due(now, self._candidate_limit):
                claim_token = uuid.uuid4().hex
                generation = uow.deliveries.try_claim(
                    candidate.id, self._worker_id, claim_token, now, lease_expires_at
                )
                if generation is None:
                    # Lost the race for this row; a peer owns it now.
                    continue
                uow.commit()
                return DeliveryClaim(
                    delivery_id=candidate.id,
                    worker_id=self._worker_id,
                    claim_token=claim_token,
                    claim_generation=generation,
                    acquired_at=now,
                    lease_expires_at=lease_expires_at,
                )
            uow.commit()
            return None


class VerifyDeliveryClaim:
    """Durably re-check a claim before acting on it. Fails closed."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def execute(self, claim: DeliveryClaim) -> bool:
        with self._uow_factory() as uow:
            active = uow.deliveries.is_claim_active(
                claim.delivery_id,
                claim.worker_id,
                claim.claim_token,
                claim.claim_generation,
                self._clock.now(),
            )
            uow.commit()
        return active


class MarkDeliveryDispatchStarted:
    """Commit the durable external side-effect boundary.

    A future dispatcher must call this immediately before its first external
    write. Once committed, a crash can no longer be recovered as "nothing was
    sent" — recovery will park the delivery as AMBIGUOUS instead.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def execute(self, claim: DeliveryClaim) -> datetime:
        with self._uow_factory() as uow:
            now = self._clock.now()
            marked = uow.deliveries.mark_dispatch_started(
                claim.delivery_id,
                claim.worker_id,
                claim.claim_token,
                claim.claim_generation,
                now,
            )
            uow.commit()
        if not marked:
            raise ClaimLost(f"dispatch boundary lost claim for delivery {claim.delivery_id}")
        return now


class PersistDeliveryOutcome:
    """Persist a lifecycle outcome under an exact active claim.

    The domain aggregate decides whether the transition is legal (so
    DELIVERED/AMBIGUOUS still require the dispatch boundary, and a
    pre-dispatch retry still cannot escape SENDING after it); the fenced
    UPDATE decides whether this worker is still allowed to write it.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def deliver(self, claim: DeliveryClaim, *, provider_message_id: str | None = None) -> None:
        def transition(delivery: OutboundDelivery, now: datetime) -> None:
            delivery.deliver(at=now, provider_message_id=provider_message_id)

        self._apply(claim, transition)

    def fail(self, claim: DeliveryClaim, *, failure_code: str, failure_message: str) -> None:
        def transition(delivery: OutboundDelivery, now: datetime) -> None:
            delivery.fail(at=now, failure_code=failure_code, failure_message=failure_message)

        self._apply(claim, transition)

    def mark_ambiguous(
        self, claim: DeliveryClaim, *, failure_code: str, failure_message: str
    ) -> None:
        def transition(delivery: OutboundDelivery, now: datetime) -> None:
            delivery.mark_ambiguous(
                at=now, failure_code=failure_code, failure_message=failure_message
            )

        self._apply(claim, transition)

    def release_for_retry(self, claim: DeliveryClaim, *, available_at: datetime) -> None:
        def transition(delivery: OutboundDelivery, now: datetime) -> None:
            delivery.release_for_retry(at=now, available_at=available_at)

        self._apply(claim, transition)

    def _apply(self, claim: DeliveryClaim, transition: _Transition) -> None:
        with self._uow_factory() as uow:
            now = self._clock.now()
            delivery = uow.deliveries.get(claim.delivery_id)
            if (
                delivery is None
                or delivery.status is not DeliveryStatus.SENDING
                or delivery.claim_owner != claim.worker_id
                or delivery.claim_token != claim.claim_token
                or delivery.claim_generation != claim.claim_generation
            ):
                raise ClaimLost(f"outcome lost claim for delivery {claim.delivery_id}")
            transition(delivery, now)
            saved = uow.deliveries.save_claimed_lifecycle(
                delivery, claim.worker_id, claim.claim_token, claim.claim_generation, now
            )
            if not saved:
                raise ClaimLost(f"outcome lost claim for delivery {claim.delivery_id}")
            uow.commit()
