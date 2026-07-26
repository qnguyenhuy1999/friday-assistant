"""CORS is a thin delivery-layer gap-fix (Phase 14): the browser control plane
needs cross-origin access to the API; nothing else about the app changes."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from apps.api.app import create_app
from apps.api.settings import ApiSettings


@pytest.fixture
def app(tmp_path: Path) -> Iterator[FastAPI]:
    application = create_app(
        ApiSettings(
            database_url=f"sqlite:///{tmp_path / 'cors.db'}",
            host="127.0.0.1",
            port=8000,
            sse_poll_interval_seconds=0.5,
        )
    )
    try:
        yield application
    finally:
        application.state.engine.dispose()


def test_allows_configured_browser_origin(app: FastAPI) -> None:
    with TestClient(app) as client:
        response = client.options(
            "/v1/tasks",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_does_not_allow_unconfigured_origin(app: FastAPI) -> None:
    with TestClient(app) as client:
        response = client.options(
            "/v1/tasks",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "POST",
            },
        )
    assert response.headers.get("access-control-allow-origin") is None


def test_allowed_origins_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRIDAY_API_CORS_ORIGINS", " https://friday.example , https://ops.example ")
    monkeypatch.setenv("FRIDAY_API_DATABASE_URL", f"sqlite:///{tmp_path / 'env.db'}")

    settings = ApiSettings.from_env()

    assert settings.cors_allowed_origins == (
        "https://friday.example",
        "https://ops.example",
    )
    application = create_app(settings)
    try:
        with TestClient(application) as client:
            response = client.options(
                "/v1/tasks",
                headers={
                    "Origin": "https://ops.example",
                    "Access-Control-Request-Method": "POST",
                },
            )
        assert response.headers["access-control-allow-origin"] == "https://ops.example"
    finally:
        application.state.engine.dispose()


def test_unset_origins_fall_back_to_the_local_dev_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FRIDAY_API_CORS_ORIGINS", raising=False)

    assert ApiSettings.from_env().cors_allowed_origins == (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )


def test_blank_origins_allow_nothing_rather_than_reverting_to_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured-but-empty value fails closed: an operator who sets the
    variable and supplies no origin gets no cross-origin access, not the
    localhost dev defaults."""
    monkeypatch.setenv("FRIDAY_API_CORS_ORIGINS", "   ,  ")

    assert ApiSettings.from_env().cors_allowed_origins == ()


@pytest.mark.parametrize("origin", ["*", "http://*.example.test", "https://example.test/path"])
def test_wildcard_or_path_cors_origin_fails_early(origin: str) -> None:
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        ApiSettings(
            database_url="sqlite:///./friday.db",
            host="127.0.0.1",
            port=8000,
            sse_poll_interval_seconds=0.5,
            cors_allowed_origins=(origin,),
        )


def test_non_loopback_bind_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRIDAY_API_HOST", "0.0.0.0")
    monkeypatch.delenv("FRIDAY_API_ALLOW_REMOTE_BIND", raising=False)

    with pytest.raises(ValueError, match="loopback"):
        ApiSettings.from_env()


def test_legacy_remote_bind_opt_in_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRIDAY_API_HOST", "127.0.0.1")
    monkeypatch.setenv("FRIDAY_API_ALLOW_REMOTE_BIND", "true")

    with pytest.raises(ValueError, match="no longer supported"):
        ApiSettings.from_env()


def test_invalid_api_environment_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRIDAY_API_PORT", "not-a-port")

    with pytest.raises(ValueError, match="FRIDAY_API_PORT must be an integer"):
        ApiSettings.from_env()


def test_invalid_request_size_setting_fails_early(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRIDAY_API_MAX_REQUEST_BYTES", "0")

    with pytest.raises(ValueError, match="max_request_bytes must be positive"):
        ApiSettings.from_env()


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_sse_poll_interval_must_be_finite(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("FRIDAY_API_SSE_POLL_INTERVAL_SECONDS", value)

    with pytest.raises(ValueError, match="finite"):
        ApiSettings.from_env()
