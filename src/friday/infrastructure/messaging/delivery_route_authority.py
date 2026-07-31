"""Adapter deliberately exposing only secret-free delivery authority."""

from __future__ import annotations

from dataclasses import dataclass

from friday.domain.delivery_route import DeliveryRouteAuthority
from friday.infrastructure.messaging.config import MessagingRoute


@dataclass(frozen=True, slots=True)
class MessagingRouteAuthorityResolver:
    routes: tuple[MessagingRoute, ...]

    def resolve(self, route_id: str) -> DeliveryRouteAuthority | None:
        route = next((item for item in self.routes if item.route_id == route_id), None)
        if route is None:
            return None
        return DeliveryRouteAuthority(
            route.route_id, route.enabled, route.fingerprint, route.max_body_chars
        )
