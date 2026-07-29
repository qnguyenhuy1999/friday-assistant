"""Transport boundary for outbound delivery.

Dispatchers depend on this protocol, never on an HTTP implementation.
"""

from __future__ import annotations

from typing import Protocol

from friday.infrastructure.messaging.transport_models import TransportRequest, TransportResult


class DeliveryTransport(Protocol):
    def send(self, request: TransportRequest) -> TransportResult: ...
