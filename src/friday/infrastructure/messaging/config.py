"""Operator-owned messaging route configuration and identity binding."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from friday.domain.errors import DomainValidationError

MAX_ROUTES = 32
MAX_ROUTE_ID_LENGTH = 64
MAX_TIMEOUT_SECONDS = 60
_ROUTE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_PAYLOAD_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


@dataclass(frozen=True, slots=True)
class MessagingRoute:
    """A resolved route held only in process memory.

    ``endpoint`` is intentionally never exposed by a tool descriptor, API
    schema, persistence mapper, or tool result.  Its digest (not its value)
    participates in the immutable fingerprint frozen on a delivery.
    """

    route_id: str
    trusted_description: str
    principal_id: str
    endpoint: str
    payload_field: str = "text"
    timeout_seconds: float = 10
    enabled: bool = True
    allow_insecure_for_tests: bool = False

    def __post_init__(self) -> None:
        if not _ROUTE_ID.fullmatch(self.route_id) or len(self.route_id) > MAX_ROUTE_ID_LENGTH:
            raise DomainValidationError("MessagingRoute.route_id is invalid")
        if not self.trusted_description.strip():
            raise DomainValidationError("MessagingRoute.trusted_description must not be empty")
        if not self.principal_id.strip():
            raise DomainValidationError("MessagingRoute.principal_id must not be empty")
        if not _PAYLOAD_FIELD.fullmatch(self.payload_field):
            raise DomainValidationError("MessagingRoute.payload_field is invalid")
        if not 0 < self.timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise DomainValidationError("MessagingRoute.timeout_seconds must be between 0 and 60")
        parsed = urlsplit(self.endpoint)
        allowed_schemes = {"https"}
        if self.allow_insecure_for_tests:
            allowed_schemes.add("http")
        if (
            parsed.scheme not in allowed_schemes
            or not parsed.netloc
            or parsed.username
            or parsed.password
        ):
            raise DomainValidationError(
                "MessagingRoute.endpoint must be an absolute safe HTTPS URL"
            )
        if parsed.fragment:
            raise DomainValidationError("MessagingRoute.endpoint must not contain a fragment")

    @property
    def fingerprint(self) -> str:
        parsed = urlsplit(self.endpoint)
        # The actual secret-bearing endpoint never enters durable state. Its
        # digest still freezes the authority granted at approval time.
        destination_digest = hashlib.sha256(self.endpoint.encode("utf-8")).hexdigest()
        canonical = json.dumps(
            {
                "v": 1,
                "route_id": self.route_id,
                "transport": "webhook",
                "principal_id": self.principal_id,
                "destination_digest": destination_digest,
                "payload_field": self.payload_field,
                "scheme": parsed.scheme,
                "timeout_seconds": self.timeout_seconds,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class MessagingRoutes:
    def __init__(self, routes: tuple[MessagingRoute, ...]) -> None:
        if len(routes) > MAX_ROUTES:
            raise DomainValidationError(f"MessagingRoutes supports at most {MAX_ROUTES} routes")
        mapping: dict[str, MessagingRoute] = {}
        normalized: set[str] = set()
        for route in routes:
            key = route.route_id.casefold()
            if key in normalized:
                raise DomainValidationError("MessagingRoutes route IDs must be unique")
            normalized.add(key)
            mapping[route.route_id] = route
        self._routes = mapping

    @property
    def enabled(self) -> bool:
        return any(route.enabled for route in self._routes.values())

    def get_enabled(self, route_id: str) -> MessagingRoute | None:
        route = self.get(route_id)
        return route if route is not None and route.enabled else None

    def get(self, route_id: str) -> MessagingRoute | None:
        """Resolve a route for safe operator-facing status only."""
        return self._routes.get(route_id)

    def safe_descriptions(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (route.route_id, route.trusted_description)
            for route in sorted(self._routes.values(), key=lambda item: item.route_id)
            if route.enabled
        )
