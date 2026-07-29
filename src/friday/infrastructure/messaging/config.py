"""Safe, offline loading of operator-owned messaging routes."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

MAX_CONFIG_BYTES = 1_000_000
MAX_ROUTES = 32
MAX_ROUTE_ID_LENGTH = 64
MAX_BODY_CHARS = 16_000
MAX_TIMEOUT_SECONDS = 60.0
_ROUTE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_PAYLOAD_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_TOP = frozenset({"version", "routes"})
_ROUTE = frozenset(
    {
        "route_id",
        "trusted_description",
        "enabled",
        "transport",
        "principal_id",
        "endpoint_env",
        "payload_field",
        "max_body_chars",
        "timeout_seconds",
    }
)


class MessagingConfigurationInvalid(ValueError):
    """A static, secret-free configuration error."""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise MessagingConfigurationInvalid(
                "messaging config must not contain duplicate object keys"
            )
        output[key] = value
    return output


def _constant(_: str) -> None:
    raise MessagingConfigurationInvalid("messaging config must use standard JSON numbers")


def _canonical_route_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > MAX_ROUTE_ID_LENGTH
        or not _ROUTE_ID.fullmatch(value)
    ):
        raise MessagingConfigurationInvalid("messaging route_id is invalid")
    return value.lower()


@dataclass(frozen=True, slots=True)
class MessagingRoute:
    route_id: str
    trusted_description: str
    enabled: bool
    transport: str
    principal_id: str
    endpoint_env: str = field(repr=False)
    endpoint: str = field(repr=False)
    payload_field: str
    max_body_chars: int
    timeout_seconds: float

    @property
    def fingerprint(self) -> str:
        destination_digest = hashlib.sha256(self.endpoint.encode("utf-8")).hexdigest()
        authority = {
            "version": 1,
            "route_id": self.route_id,
            "transport": self.transport,
            "principal_id": self.principal_id,
            "destination_digest": destination_digest,
            "payload_field": self.payload_field,
            "max_body_chars": self.max_body_chars,
            "timeout_seconds": self.timeout_seconds,
        }
        canonical = json.dumps(authority, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_messaging_routes(
    path: Path | None, environ: Mapping[str, str] | None = None
) -> tuple[MessagingRoute, ...]:
    """Load enabled and disabled routes without making any network request."""
    if path is None:
        return ()
    environment = os.environ if environ is None else environ
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise MessagingConfigurationInvalid("messaging config file could not be read") from exc
    if not raw.strip():
        return ()
    if len(raw) > MAX_CONFIG_BYTES:
        raise MessagingConfigurationInvalid("messaging config exceeded configured bytes")
    try:
        document = json.loads(raw, object_pairs_hook=_no_duplicate_keys, parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise MessagingConfigurationInvalid("messaging config must be valid JSON") from exc
    if not isinstance(document, dict):
        raise MessagingConfigurationInvalid("messaging config root must be an object")
    if set(document) - _TOP:
        raise MessagingConfigurationInvalid("messaging config contains unknown keys")
    if document.get("version", 1) != 1:
        raise MessagingConfigurationInvalid("messaging config version is unsupported")
    entries = document.get("routes", [])
    if not isinstance(entries, list) or len(entries) > MAX_ROUTES:
        raise MessagingConfigurationInvalid("messaging config routes are invalid")
    routes = tuple(_route(entry, environment) for entry in entries)
    route_ids = [route.route_id for route in routes]
    if len(set(route_ids)) != len(route_ids):
        raise MessagingConfigurationInvalid("messaging config has duplicate route IDs")
    return routes


def _route(entry: Any, environ: Mapping[str, str]) -> MessagingRoute:
    if not isinstance(entry, dict) or set(entry) - _ROUTE:
        raise MessagingConfigurationInvalid("messaging route contains invalid keys")
    route_id_value = entry.get("route_id")
    if not isinstance(route_id_value, str):
        raise MessagingConfigurationInvalid("messaging route_id is invalid")
    route_id = _canonical_route_id(route_id_value)
    description = entry.get("trusted_description")
    principal_id = entry.get("principal_id")
    endpoint_env = entry.get("endpoint_env")
    payload_field = entry.get("payload_field")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (description, principal_id, endpoint_env)
    ):
        raise MessagingConfigurationInvalid("messaging route contains an invalid required string")
    assert isinstance(description, str)
    assert isinstance(principal_id, str)
    assert isinstance(endpoint_env, str)
    if not isinstance(payload_field, str) or not _PAYLOAD_FIELD.fullmatch(payload_field):
        raise MessagingConfigurationInvalid("messaging route payload_field is invalid")
    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        raise MessagingConfigurationInvalid("messaging route enabled must be a boolean")
    if entry.get("transport") != "https_webhook":
        raise MessagingConfigurationInvalid("messaging route transport is unsupported")
    max_body = entry.get("max_body_chars")
    if (
        not isinstance(max_body, int)
        or isinstance(max_body, bool)
        or not 0 < max_body <= MAX_BODY_CHARS
    ):
        raise MessagingConfigurationInvalid("messaging route max_body_chars is invalid")
    timeout = entry.get("timeout_seconds")
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not math.isfinite(timeout)
        or not 0 < timeout <= MAX_TIMEOUT_SECONDS
    ):
        raise MessagingConfigurationInvalid("messaging route timeout_seconds is invalid")
    endpoint = ""
    if enabled:
        endpoint = environ.get(endpoint_env, "").strip()
        if not endpoint:
            raise MessagingConfigurationInvalid("enabled messaging route endpoint is unavailable")
        parsed = urlparse(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise MessagingConfigurationInvalid("enabled messaging route endpoint is invalid")
    return MessagingRoute(
        route_id,
        description.strip(),
        enabled,
        "https_webhook",
        principal_id.strip(),
        endpoint_env.strip(),
        endpoint,
        payload_field,
        max_body,
        float(timeout),
    )
