from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient

from tests.api.conftest import append_run_event, append_task_event, seed_active_run


def test_run_events_are_sequence_ordered_and_paginated_without_gaps(app: FastAPI) -> None:
    seeded = seed_active_run(app)
    for sequence in (5, 1, 4, 2, 3):
        append_run_event(app, seeded.run_id, sequence)

    with TestClient(app) as client:
        first = client.get(f"/v1/runs/{seeded.run_id}/events?limit=2")
        second = client.get(
            f"/v1/runs/{seeded.run_id}/events?limit=2&cursor={first.json()['next_cursor']}"
        )
        final = client.get(
            f"/v1/runs/{seeded.run_id}/events?limit=2&cursor={second.json()['next_cursor']}"
        )
    pages = [first.json()["items"], second.json()["items"], final.json()["items"]]
    assert [item["sequence"] for page in pages for item in page] == [1, 2, 3, 4, 5]
    assert final.json()["next_cursor"] is None


def test_event_pagination_validates_cursor_and_limit_and_honors_default_and_max(
    app: FastAPI,
) -> None:
    seeded = seed_active_run(app)
    for sequence in range(1, 22):
        append_run_event(app, seeded.run_id, sequence)
    with TestClient(app) as client:
        default = client.get(f"/v1/runs/{seeded.run_id}/events")
        maximum = client.get(f"/v1/runs/{seeded.run_id}/events?limit=100")
        invalid_limit = client.get(f"/v1/runs/{seeded.run_id}/events?limit=101")
        invalid_cursor = client.get(f"/v1/runs/{seeded.run_id}/events?cursor=not-a-cursor")
    assert len(default.json()["items"]) == 20
    assert default.json()["next_cursor"] is not None
    assert len(maximum.json()["items"]) == 21
    assert invalid_limit.status_code == 422
    assert invalid_cursor.status_code == 422


def test_task_events_use_the_same_sequence_and_pagination_contract(app: FastAPI) -> None:
    seeded = seed_active_run(app)
    for sequence in (3, 1, 2):
        append_task_event(app, seeded.task_id, sequence)
    with TestClient(app) as client:
        response = client.get(f"/v1/tasks/{seeded.task_id}/events?limit=2")
        missing = client.get("/v1/tasks/00000000-0000-4000-8000-000000000099/events")
    assert [item["sequence"] for item in response.json()["items"]] == [1, 2]
    assert response.json()["next_cursor"] is not None
    assert missing.status_code == 404
