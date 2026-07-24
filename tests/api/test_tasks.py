from __future__ import annotations

from starlette.testclient import TestClient


def test_task_create_get_list_and_cancel(client: TestClient) -> None:
    created = client.post("/v1/tasks", json={"title": "Ship", "description": "API"})
    assert created.status_code == 201
    task = created.json()
    assert task["status"] == "pending"
    assert task["created_at"].endswith("Z")

    assert client.get(f"/v1/tasks/{task['id']}").json()["id"] == task["id"]
    assert client.get("/v1/tasks").json()["items"] == [task]
    assert client.post(f"/v1/tasks/{task['id']}/cancel").json()["status"] == "cancelled"
    assert client.get("/v1/tasks/not-a-uuid").status_code == 422
    assert client.get("/v1/tasks/00000000-0000-0000-0000-000000000000").status_code == 404
