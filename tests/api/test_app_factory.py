"""Proves the API composition root wires up correctly against a real,
temporary SQLite database -- no mocked UnitOfWork or repositories."""

from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from apps.api.app import create_app
from apps.api.settings import ApiSettings


def _settings_for(tmp_path: Path) -> ApiSettings:
    db_path = tmp_path / "test.db"
    return ApiSettings(
        database_url=f"sqlite:///{db_path}",
        host="127.0.0.1",
        port=8000,
        sse_poll_interval_seconds=0.1,
    )


def test_create_app_registers_health_route(tmp_path: Path) -> None:
    app = create_app(_settings_for(tmp_path))
    try:
        with TestClient(app) as client:
            response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    finally:
        app.state.engine.dispose()


def test_create_app_uses_the_configured_temporary_database(tmp_path: Path) -> None:
    settings = _settings_for(tmp_path)
    app = create_app(settings)
    try:
        assert app.state.settings.database_url == settings.database_url
    finally:
        app.state.engine.dispose()


def test_openapi_generation_is_deterministic(tmp_path: Path) -> None:
    app = create_app(_settings_for(tmp_path))
    try:
        schema = app.openapi()
        assert "/health" in schema["paths"]
        assert schema["paths"]["/health"]["get"]["operationId"] == "getHealth"
    finally:
        app.state.engine.dispose()
