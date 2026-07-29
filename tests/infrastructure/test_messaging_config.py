from __future__ import annotations

import json
from pathlib import Path

import pytest

from friday.infrastructure.messaging.config import (
    MessagingConfigurationInvalid,
    MessagingRoute,
    load_messaging_routes,
)

ENDPOINT = "https://fixture.example.test/hooks/unique-secret"


def _route(**overrides: object) -> dict[str, object]:
    route: dict[str, object] = {
        "route_id": "ops.alerts",
        "trusted_description": "Operations alerts",
        "enabled": True,
        "transport": "https_webhook",
        "principal_id": "ops",
        "endpoint_env": "FIXTURE_MESSAGE_ENDPOINT",
        "payload_field": "body",
        "max_body_chars": 100,
        "timeout_seconds": 10,
    }
    route.update(overrides)
    return route


def _load(
    tmp_path: Path, document: object, env: dict[str, str] | None = None
) -> tuple[MessagingRoute, ...]:
    path = tmp_path / "messaging.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return load_messaging_routes(path, {"FIXTURE_MESSAGE_ENDPOINT": ENDPOINT, **(env or {})})


def test_absent_or_blank_config_disables_messaging(tmp_path: Path) -> None:
    assert load_messaging_routes(None) == ()
    path = tmp_path / "blank.json"
    path.write_text(" \n", encoding="utf-8")
    assert load_messaging_routes(path) == ()


def test_routes_are_safe_and_fingerprint_binds_authority(tmp_path: Path) -> None:
    first = _load(tmp_path, {"routes": [_route()]})[0]
    changed_endpoint = _load(
        tmp_path,
        {"routes": [_route()]},
        {"FIXTURE_MESSAGE_ENDPOINT": "https://other.example.test/x"},
    )[0]
    changed_description = _load(tmp_path, {"routes": [_route(trusted_description="Changed")]})[0]
    assert ENDPOINT not in repr(first)
    assert first.fingerprint != changed_endpoint.fingerprint
    assert first.fingerprint == changed_description.fingerprint


@pytest.mark.parametrize(
    "document",
    [
        {"routes": [_route(transport="http")]},
        {"routes": [_route(max_body_chars=0)]},
        {"routes": [_route(timeout_seconds=61)]},
        {"routes": [_route()], "unknown": True},
    ],
)
def test_invalid_route_authority_is_rejected(tmp_path: Path, document: object) -> None:
    with pytest.raises(MessagingConfigurationInvalid):
        _load(tmp_path, document)


def test_disabled_route_does_not_resolve_endpoint(tmp_path: Path) -> None:
    routes = _load(tmp_path, {"routes": [_route(enabled=False)]}, {})
    assert routes[0].enabled is False


@pytest.mark.parametrize("value", ["NaN", "Infinity"])
def test_nonstandard_json_is_rejected(tmp_path: Path, value: str) -> None:
    path = tmp_path / "messaging.json"
    path.write_text('{"routes": ' + value + "}", encoding="utf-8")
    with pytest.raises(MessagingConfigurationInvalid):
        load_messaging_routes(path)
