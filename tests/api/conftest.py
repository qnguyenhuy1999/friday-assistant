from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from apps.api.app import create_app
from apps.api.settings import ApiSettings
from friday.infrastructure.persistence.models import Base


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = ApiSettings(
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
        host="127.0.0.1",
        port=8000,
        sse_poll_interval_seconds=0.1,
    )
    app = create_app(settings)
    Base.metadata.create_all(app.state.engine)
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.state.engine.dispose()
