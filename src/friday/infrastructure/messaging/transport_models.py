"""Secret-safe values exchanged between the dispatcher and transports."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from friday.infrastructure.messaging.config import MessagingRoute


class TransportOutcome(StrEnum):
    DELIVERED = "delivered"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class TransportRequest:
    """A single webhook request. Sensitive fields are deliberately not repr'd."""

    route: MessagingRoute = field(repr=False)
    body: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class TransportResult:
    outcome: TransportOutcome
    failure_code: str | None = None

    @classmethod
    def delivered(cls) -> TransportResult:
        return cls(TransportOutcome.DELIVERED)

    @classmethod
    def failed(cls, failure_code: str) -> TransportResult:
        return cls(TransportOutcome.FAILED, failure_code)
