from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast

from fastapi import FastAPI
from starlette.testclient import TestClient

from friday.domain.identifiers import RunId, ScheduleFireId, ScheduleId
from friday.domain.run import Run
from friday.domain.schedule_fire import ScheduleFire
from friday.infrastructure.messaging.config import MessagingRoute, MessagingRoutes
from tests.api.conftest import NOW, seed_active_run


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


def _create(
    client: TestClient,
    task_id: str,
    *,
    kind: str = "cron",
    cron: str | None = "0 9 * * *",
    run_at: str | None = None,
) -> dict[str, object]:
    body: dict[str, object] = {"kind": kind, "timezone": "UTC"}
    if cron is not None:
        body["cron"] = cron
    if run_at is not None:
        body["run_at"] = run_at
    response = client.post(f"/v1/tasks/{task_id}/schedules", json=body)
    assert response.status_code == 201
    return cast(dict[str, object], response.json())


def test_schedule_create_get_list_and_cursor_pagination(app: FastAPI) -> None:
    seeded = seed_active_run(app)
    app.state.clock = _Clock(NOW)
    with TestClient(app) as client:
        first = _create(client, str(seeded.task_id))
        second = _create(
            client,
            str(seeded.task_id),
            kind="once",
            cron=None,
            run_at=(NOW + timedelta(days=1)).isoformat(),
        )
        page_one = client.get(f"/v1/tasks/{seeded.task_id}/schedules?limit=1")
        page_two = client.get(
            f"/v1/tasks/{seeded.task_id}/schedules",
            params={"limit": 1, "cursor": page_one.json()["next_cursor"]},
        )
        fetched = client.get(f"/v1/tasks/{seeded.task_id}/schedules/{second['id']}")

    assert first["kind"] == "cron"
    assert page_one.status_code == page_two.status_code == fetched.status_code == 200
    assert page_one.json()["next_cursor"] is not None
    assert page_two.json()["next_cursor"] is None
    assert {
        page_one.json()["items"][0]["id"],
        page_two.json()["items"][0]["id"],
    } == {first["id"], second["id"]}
    assert fetched.json()["id"] == second["id"]


def test_schedule_controls_and_invalid_transitions(app: FastAPI) -> None:
    seeded = seed_active_run(app)
    app.state.clock = _Clock(NOW)
    with TestClient(app) as client:
        created = _create(client, str(seeded.task_id))
        path = f"/v1/tasks/{seeded.task_id}/schedules/{created['id']}"
        paused = client.post(f"{path}/pause")
        resumed = client.post(f"{path}/resume")
        cancelled = client.post(f"{path}/cancel")
        conflict = client.post(f"{path}/resume")

    assert paused.json()["status"] == "paused"
    assert resumed.json()["status"] == "active"
    assert cancelled.json()["status"] == "cancelled"
    assert conflict.status_code == 409


def test_schedule_exposes_only_safe_delivery_route_metadata(app: FastAPI) -> None:
    seeded = seed_active_run(app)
    app.state.clock = _Clock(NOW)
    app.state.messaging_routes = MessagingRoutes(
        (
            MessagingRoute(
                route_id="personal.notifications",
                trusted_description="Personal notification channel",
                principal_id="operator",
                endpoint="https://example.test/credential-in-url",
            ),
        )
    )

    with TestClient(app) as client:
        response = client.post(
            f"/v1/tasks/{seeded.task_id}/schedules",
            json={
                "kind": "cron",
                "cron": "0 9 * * *",
                "timezone": "UTC",
                "delivery_route_id": "personal.notifications",
            },
        )
        assert response.status_code == 201, response.text
        fetched = client.get(f"/v1/tasks/{seeded.task_id}/schedules/{response.json()['id']}")

    assert fetched.status_code == 200
    body = fetched.json()
    assert body["delivery_route_id"] == "personal.notifications"
    assert body["delivery_route_description"] == "Personal notification channel"
    assert body["delivery_enabled"] is True
    assert "credential-in-url" not in fetched.text


def test_schedule_validation_parent_fencing_and_fire_listing(app: FastAPI) -> None:
    seeded = seed_active_run(app)
    other = seed_active_run(app)
    app.state.clock = _Clock(NOW)
    with TestClient(app) as client:
        malformed = client.post(
            f"/v1/tasks/{seeded.task_id}/schedules",
            json={"kind": "once", "cron": "* * * * *", "timezone": "UTC"},
        )
        created = _create(client, str(seeded.task_id))
        wrong_parent = client.get(f"/v1/tasks/{other.task_id}/schedules/{created['id']}")

        schedule_id = str(created["id"])
        run = Run.new(id=RunId.new(), task_id=seeded.task_id, created_at=NOW)
        with app.state.uow_factory() as uow:
            uow.runs.add(run)
            uow.schedule_fires.add(
                ScheduleFire.new(
                    id=ScheduleFireId.new(),
                    schedule_id=ScheduleId.parse(schedule_id),
                    scheduled_for=NOW + timedelta(minutes=1),
                    fired_at=NOW + timedelta(minutes=1),
                    run_id=run.id,
                )
            )
            uow.commit()

        fires = client.get(f"/v1/tasks/{seeded.task_id}/schedules/{schedule_id}/fires")
        invalid_cursor = client.get(
            f"/v1/tasks/{seeded.task_id}/schedules/{schedule_id}/fires",
            params={"cursor": "invalid"},
        )

    assert malformed.status_code == 422
    assert wrong_parent.status_code == 404
    assert fires.status_code == 200
    assert fires.json()["items"][0]["run_id"] == str(run.id)
    assert invalid_cursor.status_code == 422
