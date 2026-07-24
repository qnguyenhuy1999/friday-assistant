from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from starlette.testclient import TestClient

from apps.api.routes.events import stream_run_events
from tests.api.conftest import append_run_event, seed_active_run


class DisconnectProbe:
    def __init__(self, states: Sequence[bool]) -> None:
        self._states = iter(states)

    async def is_disconnected(self) -> bool:
        return next(self._states, True)


async def _next_chunk(response: StreamingResponse) -> str:
    body_iterator = cast(AsyncIterator[str | bytes], response.body_iterator)
    chunk = await anext(body_iterator)
    return chunk.decode() if isinstance(chunk, bytes) else chunk


def test_sse_streams_committed_events_in_sequence_and_sets_headers(app: FastAPI) -> None:
    seeded = seed_active_run(app)
    append_run_event(app, seeded.run_id, 1)
    append_run_event(app, seeded.run_id, 2)

    async def read() -> tuple[str, str, str]:
        response = await stream_run_events(
            str(seeded.run_id),
            DisconnectProbe([False, False]),  # type: ignore[arg-type]
            app.state.uow_factory,
            app.state.clock,
            app.state.settings,
            None,
        )
        return (
            response.headers["content-type"],
            await _next_chunk(response),
            await _next_chunk(response),
        )

    content_type, first, second = asyncio.run(read())
    assert content_type.startswith("text/event-stream")
    assert "id: 1\nevent: run_started\n" in first
    assert '"sequence":1' in first
    assert "id: 2\nevent: run_started\n" in second


def test_sse_last_event_id_reconnect_and_new_events_never_duplicate(app: FastAPI) -> None:
    seeded = seed_active_run(app)
    append_run_event(app, seeded.run_id, 1)

    async def read_resume() -> tuple[str, str]:
        first_response = await stream_run_events(
            str(seeded.run_id),
            DisconnectProbe([False]),  # type: ignore[arg-type]
            app.state.uow_factory,
            app.state.clock,
            app.state.settings,
            None,
        )
        first = await _next_chunk(first_response)
        append_run_event(app, seeded.run_id, 2)
        resumed_response = await stream_run_events(
            str(seeded.run_id),
            DisconnectProbe([False]),  # type: ignore[arg-type]
            app.state.uow_factory,
            app.state.clock,
            app.state.settings,
            "1",
        )
        return first, await _next_chunk(resumed_response)

    first, resumed = asyncio.run(read_resume())
    assert "id: 1" in first
    assert "id: 2" in resumed
    assert "id: 1" not in resumed


def test_sse_disconnect_stops_before_another_poll_and_closes_sessions(app: FastAPI) -> None:
    seeded = seed_active_run(app)
    append_run_event(app, seeded.run_id, 1)

    async def disconnect() -> None:
        response = await stream_run_events(
            str(seeded.run_id),
            DisconnectProbe([False, True]),  # type: ignore[arg-type]
            app.state.uow_factory,
            app.state.clock,
            app.state.settings,
            None,
        )
        await _next_chunk(response)
        with pytest.raises(StopAsyncIteration):
            await _next_chunk(response)

    asyncio.run(disconnect())
    assert "Checked out connections: 0" in app.state.engine.pool.status()


def test_sse_rejects_bad_last_event_id_and_missing_run_before_streaming(app: FastAPI) -> None:
    with TestClient(app) as client:
        malformed = client.get(
            "/v1/runs/00000000-0000-4000-8000-000000000001/events/stream",
            headers={"Last-Event-ID": "not-a-sequence"},
        )
        missing = client.get("/v1/runs/00000000-0000-4000-8000-000000000001/events/stream")
    assert malformed.status_code == 422
    assert missing.status_code == 404
