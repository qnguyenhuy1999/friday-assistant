"""Strict, opt-in operator configuration for outbound messaging."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from friday.infrastructure.messaging.config import MessagingRoute, MessagingRoutes

_ENV_NAME = "FRIDAY_MESSAGING_ROUTES_JSON"
_MAX_CONFIG_BYTES = 1_000_000
_ROUTE_KEYS = frozenset(
    {
        "route_id",
        "enabled",
        "trusted_description",
        "principal_id",
        "endpoint_env",
        "payload_field",
        "timeout_seconds",
    }
)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("messaging configuration has a duplicate JSON key")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class MessagingSettings:
    routes: MessagingRoutes

    @property
    def enabled(self) -> bool:
        return self.routes.enabled

    def validate_lease(self, lease_duration: timedelta) -> None:
        # DeliveryDispatcher has no DB transaction around I/O but still needs
        # its fence to remain valid throughout the bounded request.
        for route_id, _ in self.routes.safe_descriptions():
            route = self.routes.get_enabled(route_id)
            assert route is not None
            if route.timeout_seconds >= lease_duration.total_seconds():
                raise ValueError("messaging route timeout must be shorter than the worker lease")

    @classmethod
    def from_env(cls) -> MessagingSettings:
        raw = os.environ.get(_ENV_NAME)
        if raw is None or not raw.strip():
            return cls(MessagingRoutes(()))
        if len(raw.encode("utf-8")) > _MAX_CONFIG_BYTES:
            raise ValueError("messaging configuration exceeds 1 MB")
        try:
            parsed = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError("messaging configuration must be valid JSON") from exc
        if not isinstance(parsed, list):
            raise ValueError("messaging configuration must be a JSON array")
        routes: list[MessagingRoute] = []
        for item in parsed:
            if not isinstance(item, dict) or set(item) - _ROUTE_KEYS:
                raise ValueError("messaging route contains unknown or invalid configuration")
            required = {
                "route_id",
                "trusted_description",
                "principal_id",
                "endpoint_env",
            }
            if not required <= set(item):
                raise ValueError("messaging route is missing required configuration")
            endpoint_env = item["endpoint_env"]
            if not isinstance(endpoint_env, str) or not endpoint_env.strip():
                raise ValueError("messaging route endpoint_env must be a non-empty string")
            endpoint = os.environ.get(endpoint_env)
            if not endpoint:
                raise ValueError("messaging route endpoint environment variable is missing")
            routes.append(
                MessagingRoute(
                    route_id=_string(item, "route_id"),
                    trusted_description=_string(item, "trusted_description"),
                    principal_id=_string(item, "principal_id"),
                    endpoint=endpoint,
                    payload_field=_optional_string(item, "payload_field", "text"),
                    timeout_seconds=_optional_number(item, "timeout_seconds", 10),
                    enabled=_optional_bool(item, "enabled", True),
                )
            )
        return cls(MessagingRoutes(tuple(routes)))


def _string(value: dict[str, Any], key: str) -> str:
    item = value[key]
    if not isinstance(item, str):
        raise ValueError(f"messaging route {key} must be a string")
    return item


def _optional_string(value: dict[str, Any], key: str, default: str) -> str:
    item = value.get(key, default)
    if not isinstance(item, str):
        raise ValueError(f"messaging route {key} must be a string")
    return item


def _optional_number(value: dict[str, Any], key: str, default: float) -> float:
    item = value.get(key, default)
    if not isinstance(item, (int, float)) or isinstance(item, bool):
        raise ValueError(f"messaging route {key} must be a number")
    return float(item)


def _optional_bool(value: dict[str, Any], key: str, default: bool) -> bool:
    item = value.get(key, default)
    if not isinstance(item, bool):
        raise ValueError(f"messaging route {key} must be a boolean")
    return item
