"""Claim-fenced outbound delivery orchestration.

The dispatcher deliberately never has a UnitOfWork open while calling a
transport.  It also treats all uncertainty after that call boundary as
AMBIGUOUS; a message is never automatically resent merely because a response
was lost.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from friday.application.delivery_lifecycle import (
    ClaimNextDelivery,
    DeliveryClaim,
    PersistDeliveryOutcome,
    VerifyDeliveryClaim,
)
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.retry_policy import RetryPolicy
from friday.domain.failure import Failure, FailureCause
from friday.domain.outbound_delivery import DeliveryStatus, OutboundDelivery


class TransportOutcomeKind(StrEnum):
    DELIVERED = "delivered"
    PRE_DISPATCH_FAILURE = "pre_dispatch_failure"
    DEFINITE_FAILURE = "definite_failure"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class TransportOutcome:
    kind: TransportOutcomeKind
    failure_code: str | None = None
    failure_message: str | None = None
    provider_message_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind is TransportOutcomeKind.DELIVERED:
            if self.failure_code is not None or self.failure_message is not None:
                raise ValueError("a delivered transport outcome cannot have failure data")
        elif not (self.failure_code or "").strip() or not (self.failure_message or "").strip():
            raise ValueError("a non-delivered transport outcome requires safe failure data")


class MessagingRoute(Protocol):
    @property
    def route_id(self) -> str: ...

    @property
    def fingerprint(self) -> str: ...

    @property
    def endpoint(self) -> str: ...

    @property
    def payload_field(self) -> str: ...

    @property
    def timeout_seconds(self) -> float: ...


class MessagingRoutes(Protocol):
    def get_enabled(self, route_id: str) -> MessagingRoute | None: ...


class MessageTransport(Protocol):
    def deliver(self, route: MessagingRoute, delivery: OutboundDelivery) -> TransportOutcome: ...


class DeliveryDispatcher:
    def __init__(
        self,
        *,
        claim_next: ClaimNextDelivery,
        verify_claim: VerifyDeliveryClaim,
        persist_outcome: PersistDeliveryOutcome,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        routes: MessagingRoutes,
        transport: MessageTransport,
        retry_policy: RetryPolicy,
    ) -> None:
        self._claim_next = claim_next
        self._verify_claim = verify_claim
        self._persist_outcome = persist_outcome
        self._uow_factory = uow_factory
        self._clock = clock
        self._routes = routes
        self._transport = transport
        self._retry_policy = retry_policy

    def dispatch_once(self) -> bool:
        claim = self._claim_next.execute()
        if claim is None:
            return False
        with self._uow_factory() as uow:
            delivery = uow.deliveries.get(claim.delivery_id)
            uow.commit()
        if delivery is None:
            return True

        route = self._routes.get_enabled(delivery.route_id)
        if route is None or route.fingerprint != delivery.route_fingerprint:
            self._persist_failure(
                claim,
                delivery,
                DeliveryStatus.FAILED,
                "route_binding_changed",
                "the approved messaging route no longer has the same binding",
            )
            return True

        # Last durable fence before crossing the side-effect boundary.
        if not self._verify_claim.execute(claim):
            return True
        outcome = self._transport.deliver(route, delivery)
        if outcome.kind is TransportOutcomeKind.DELIVERED:
            delivery.deliver(at=self._clock.now(), provider_message_id=outcome.provider_message_id)
            self._persist_outcome.execute(claim, delivery)
        elif outcome.kind is TransportOutcomeKind.PRE_DISPATCH_FAILURE:
            self._handle_pre_dispatch_failure(claim, delivery, outcome)
        elif outcome.kind is TransportOutcomeKind.DEFINITE_FAILURE:
            self._persist_failure(
                claim,
                delivery,
                DeliveryStatus.FAILED,
                outcome.failure_code,
                outcome.failure_message,
            )
        else:
            self._persist_failure(
                claim,
                delivery,
                DeliveryStatus.AMBIGUOUS,
                outcome.failure_code,
                outcome.failure_message,
            )
        return True

    def _handle_pre_dispatch_failure(
        self, claim: DeliveryClaim, delivery: OutboundDelivery, outcome: TransportOutcome
    ) -> None:
        failure = Failure(
            code=outcome.failure_code or "pre_dispatch_failure",
            # A transport implementation may have observed untrusted remote
            # text or an endpoint-bearing exception.  Persist only Friday's
            # stable explanation; never delegate durable error text to it.
            message="delivery could not be dispatched before an external effect",
            retryable=True,
            cause=FailureCause.TOOL,
        )
        if self._retry_policy.is_retry_allowed(delivery.attempt_count, failure):
            delivery.requeue(
                at=self._clock.now(),
                available_at=self._clock.now()
                + self._retry_policy.compute_delay(delivery.attempt_count + 1),
            )
            self._persist_outcome.execute(claim, delivery)
            return
        self._persist_failure(claim, delivery, DeliveryStatus.FAILED, failure.code, failure.message)

    def _persist_failure(
        self,
        claim: DeliveryClaim,
        delivery: OutboundDelivery,
        status: DeliveryStatus,
        code: str | None,
        message: str | None,
    ) -> None:
        safe_code = code or "delivery_failed"
        del message
        safe_message = (
            "delivery outcome is unknown after dispatch"
            if status is DeliveryStatus.AMBIGUOUS
            else "delivery failed before a confirmed external effect"
        )
        if status is DeliveryStatus.FAILED:
            delivery.fail(
                at=self._clock.now(), failure_code=safe_code, failure_message=safe_message
            )
        else:
            delivery.mark_ambiguous(
                at=self._clock.now(), failure_code=safe_code, failure_message=safe_message
            )
        self._persist_outcome.execute(claim, delivery)
