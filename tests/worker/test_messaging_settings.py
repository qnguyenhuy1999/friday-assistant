from __future__ import annotations

import json

import pytest

from apps.worker.messaging_settings import MessagingSettings


def _route(**overrides: object) -> str:
    value: dict[str, object] = {
        "route_id": "personal.notifications",
        "trusted_description": "Personal notifications",
        "principal_id": "personal",
        "endpoint_env": "TEST_NOTIFICATION_WEBHOOK",
    }
    value.update(overrides)
    return json.dumps([value])


def test_messaging_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRIDAY_MESSAGING_ROUTES_JSON", raising=False)
    assert MessagingSettings.from_env().enabled is False


def test_operator_config_resolves_endpoint_without_exposing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_NOTIFICATION_WEBHOOK", "https://example.test/secret")
    monkeypatch.setenv("FRIDAY_MESSAGING_ROUTES_JSON", _route())

    settings = MessagingSettings.from_env()
    assert settings.routes.safe_descriptions() == (
        ("personal.notifications", "Personal notifications"),
    )
    assert "secret" not in repr(settings.routes.safe_descriptions())


@pytest.mark.parametrize(
    "raw",
    [
        '{"route_id":"a","route_id":"b"}',
        json.dumps([{"route_id": "a"}]),
        _route(unknown="value"),
    ],
)
def test_messaging_configuration_rejects_unsafe_or_ambiguous_shapes(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("TEST_NOTIFICATION_WEBHOOK", "https://example.test/secret")
    monkeypatch.setenv("FRIDAY_MESSAGING_ROUTES_JSON", raw)
    with pytest.raises(ValueError):
        MessagingSettings.from_env()


def test_messaging_configuration_requires_referenced_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TEST_NOTIFICATION_WEBHOOK", raising=False)
    monkeypatch.setenv("FRIDAY_MESSAGING_ROUTES_JSON", _route())
    with pytest.raises(ValueError, match="environment variable is missing"):
        MessagingSettings.from_env()
