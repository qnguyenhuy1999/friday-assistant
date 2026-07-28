"""Durable delivery lease operations.

This layer owns the database-side fencing for outbound side effects.  It
contains no transport calls: callers must take the returned claim, perform a
fresh ``VerifyDeliveryClaim`` immediately before I/O, then persist a terminal
outcome through ``PersistDeliveryOutcome``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from friday.application.errors import ClaimLost
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.domain.identifiers import DeliveryId
from friday.domain.outbound_delivery import OutboundDelivery


@dataclass(frozen=True, slots=True)
class DeliveryClaim:
    delivery_id: DeliveryId
    worker_id: str
    claim_token: str
    claim_generation: int
    acquired_at: datetime
    lease_expires_at: datetime


class ClaimNextDelivery:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        *,
        worker_id: str,
        lease_duration: timedelta,
        candidate_limit: int,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if candidate_limit < 1:
            raise ValueError("candidate_limit must be at least one")
        self._uow_factory = uow_factory
        self._clock = clock
        self._worker_id = worker_id
        self._lease_duration = lease_duration
        self._candidate_limit = candidate_limit

    def execute(self) -> DeliveryClaim | None:
        with self._uow_factory() as uow:
            now = self._clock.now()
            # A SENDING delivery may already have reached its destination.
            # Never put it back in QUEUED after a lease loss.
            uow.deliveries.recover_expired_sending(now)
            for delivery in uow.deliveries.list_due(now, self._candidate_limit):
                token = uuid.uuid4().hex
                expires_at = now + self._lease_duration
                generation = uow.deliveries.try_claim(
                    delivery.id, self._worker_id, token, now, expires_at
                )
                if generation is not None:
                    uow.commit()
                    return DeliveryClaim(
                        delivery_id=delivery.id,
                        worker_id=self._worker_id,
                        claim_token=token,
                        claim_generation=generation,
                        acquired_at=now,
                        lease_expires_at=expires_at,
                    )
            uow.commit()
        return None


class VerifyDeliveryClaim:
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


class PersistDeliveryOutcome:
    """Fenced Txn B persistence for a delivery outcome.

    The aggregate supplied here must already have made a legal transition
    from SENDING.  A stale worker is rejected before its outcome can replace
    the current durable state.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def execute(self, claim: DeliveryClaim, delivery: OutboundDelivery) -> None:
        if delivery.id != claim.delivery_id:
            raise ValueError("delivery outcome does not match its claim")
        with self._uow_factory() as uow:
            saved = uow.deliveries.save_if_claimed(
                delivery,
                claim.worker_id,
                claim.claim_token,
                claim.claim_generation,
                self._clock.now(),
            )
            uow.commit()
        if not saved:
            raise ClaimLost(f"delivery outcome lost claim for delivery {claim.delivery_id}")
