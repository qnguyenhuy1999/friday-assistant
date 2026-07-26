from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from starlette.testclient import TestClient

from apps.api.app import create_app
from apps.api.settings import ApiSettings


def _app(tmp_path: Path, *, max_request_bytes: int) -> FastAPI:
    app = create_app(
        ApiSettings(
            database_url=f"sqlite:///{tmp_path / 'request-size.db'}",
            host="127.0.0.1",
            port=8000,
            sse_poll_interval_seconds=0.5,
            max_request_bytes=max_request_bytes,
        )
    )
    return app


def test_request_at_the_ceiling_reaches_route_validation(tmp_path: Path) -> None:
    app = _app(tmp_path, max_request_bytes=64)
    try:
        with TestClient(app) as client:
            response = client.post("/v1/tasks", content=b'{"title":"small"}')
        assert response.status_code != 413
    finally:
        app.state.engine.dispose()


def test_oversized_request_is_rejected_before_route_processing(tmp_path: Path) -> None:
    app = _app(tmp_path, max_request_bytes=16)
    try:
        with TestClient(app) as client:
            response = client.post("/v1/tasks", content=b'{"title":"this is too large"}')
        assert response.status_code == 413
        assert response.json() == {"detail": "request body exceeds limit"}
    finally:
        app.state.engine.dispose()
