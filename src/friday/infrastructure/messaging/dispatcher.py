"""Claim, authorize, and dispatch one durable outbound delivery."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from friday.application.delivery_lifecycle import (
    DeliveryClaim,
    MarkDeliveryDispatchStarted,
    PersistDeliveryOutcome,
)
from friday.application.errors import ClaimLost
from friday.application.ports import UnitOfWorkFactory
from friday.domain.identifiers import DeliveryId
from friday.domain.outbound_delivery import OutboundDelivery
from friday.infrastructure.messaging.config import MessagingRoute
from friday.infrastructure.messaging.transport import DeliveryTransport
from friday.infrastructure.messaging.transport_models import (
    TransportOutcome,
    TransportRequest,
)

ROUTE_MISSING = "delivery_route_missing"
ROUTE_DISABLED = "delivery_route_disabled"
ROUTE_FINGERPRINT_MISMATCH = "delivery_route_fingerprint_mismatch"
ALREADY_DISPATCHED = "delivery_already_dispatched"
TRANSPORT_EXCEPTION = "delivery_transport_exception"

_ROUTE_MISSING_MESSAGE = "Delivery route is no longer configured; no message was sent."
_ROUTE_DISABLED_MESSAGE = "Delivery route is disabled; no message was sent."
_ROUTE_FINGERPRINT_MESSAGE = "Delivery route authority changed; no message was sent."
_ALREADY_DISPATCHED_MESSAGE = "Delivery was already marked for dispatch; no message was sent."
_TRANSPORT_EXCEPTION_MESSAGE = "Webhook transport failed without a delivery result."


class DispatchResult(StrEnum):
    IDLE = "idle"
    CLAIM_LOST = "claim_lost"
    DELIVERED = "delivered"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


class DeliveryClaimer(Protocol):
    def execute(self) -> DeliveryClaim | None: ...


@dataclass(frozen=True, slots=True)
class DeliveryDispatcher:
    """Dispatch one claim at a time with no transaction spanning network I/O."""

    claim_next: DeliveryClaimer
    dispatch_started: MarkDeliveryDispatchStarted
    persist_outcome: PersistDeliveryOutcome
    uow_factory: UnitOfWorkFactory
    routes: tuple[MessagingRoute, ...]
    transport: DeliveryTransport

    def dispatch_once(self) -> DispatchResult:
        claim = self.claim_next.execute()
        if claim is None:
            return DispatchResult.IDLE
        delivery = self._load_claimed_delivery(claim.delivery_id)
        if delivery is None:
            return DispatchResult.CLAIM_LOST
        if delivery.dispatch_started_at is not None:
            if not self._persist(
                lambda: self.persist_outcome.mark_ambiguous(
                    claim,
                    failure_code=ALREADY_DISPATCHED,
                    failure_message=_ALREADY_DISPATCHED_MESSAGE,
                )
            ):
                return DispatchResult.CLAIM_LOST
            return DispatchResult.AMBIGUOUS
        route = next((item for item in self.routes if item.route_id == delivery.route_id), None)
        if route is None:
            if not self._persist(
                lambda: self.persist_outcome.fail(
                    claim, failure_code=ROUTE_MISSING, failure_message=_ROUTE_MISSING_MESSAGE
                )
            ):
                return DispatchResult.CLAIM_LOST
            return DispatchResult.FAILED
        if not route.enabled:
            if not self._persist(
                lambda: self.persist_outcome.fail(
                    claim, failure_code=ROUTE_DISABLED, failure_message=_ROUTE_DISABLED_MESSAGE
                )
            ):
                return DispatchResult.CLAIM_LOST
            return DispatchResult.FAILED
        if route.fingerprint != delivery.route_fingerprint:
            if not self._persist(
                lambda: self.persist_outcome.mark_route_ambiguous(
                    claim,
                    failure_code=ROUTE_FINGERPRINT_MISMATCH,
                    failure_message=_ROUTE_FINGERPRINT_MESSAGE,
                )
            ):
                return DispatchResult.CLAIM_LOST
            return DispatchResult.AMBIGUOUS
        try:
            self.dispatch_started.execute(claim)
        except ClaimLost:
            return DispatchResult.IDLE
        try:
            result = self.transport.send(TransportRequest(route, delivery.body))
        except Exception:  # noqa: BLE001 - never expose transport exception details
            if not self._persist(
                lambda: self.persist_outcome.mark_ambiguous(
                    claim,
                    failure_code=TRANSPORT_EXCEPTION,
                    failure_message=_TRANSPORT_EXCEPTION_MESSAGE,
                )
            ):
                return DispatchResult.CLAIM_LOST
            return DispatchResult.AMBIGUOUS
        if result.outcome is TransportOutcome.DELIVERED:
            if not self._persist(lambda: self.persist_outcome.deliver(claim)):
                return DispatchResult.CLAIM_LOST
            return DispatchResult.DELIVERED
        if result.outcome is TransportOutcome.AMBIGUOUS:
            if not self._persist(
                lambda: self.persist_outcome.mark_ambiguous(
                    claim,
                    failure_code=result.failure_code or "webhook_transport_error",
                    failure_message="Webhook delivery outcome is unknown.",
                )
            ):
                return DispatchResult.CLAIM_LOST
            return DispatchResult.AMBIGUOUS
        if not self._persist(
            lambda: self.persist_outcome.fail(
                claim,
                failure_code=result.failure_code or "webhook_transport_error",
                failure_message="Webhook delivery failed.",
            )
        ):
            return DispatchResult.CLAIM_LOST
        return DispatchResult.FAILED

    @staticmethod
    def _persist(action: Callable[[], None]) -> bool:
        """Return false when recovery has fenced this worker out."""
        try:
            action()
        except ClaimLost:
            return False
        return True

    def _load_claimed_delivery(self, delivery_id: DeliveryId) -> OutboundDelivery | None:
        with self.uow_factory() as uow:
            delivery = uow.deliveries.get(delivery_id)
            uow.commit()
            return delivery
