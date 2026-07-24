from __future__ import annotations

from starlette.testclient import TestClient


def test_step_create_and_lifecycle(client: TestClient) -> None:
    task = client.post("/v1/tasks", json={"title": "Ship"}).json()
    run_id = client.post(f"/v1/tasks/{task['id']}/runs").json()["run_id"]
    client.post(f"/v1/runs/{run_id}/start")
    created = client.post(f"/v1/runs/{run_id}/steps", json={"name": "build"})
    assert created.status_code == 201
    step_id = created.json()["id"]
    assert client.get(f"/v1/steps/{step_id}").json()["position"] == 0
    assert client.post(f"/v1/steps/{step_id}/start").json()["status"] == "running"
    assert client.post(f"/v1/steps/{step_id}/complete").json()["status"] == "succeeded"
    assert client.post(f"/v1/steps/{step_id}/skip").status_code == 409
    assert client.get("/v1/steps/00000000-0000-0000-0000-000000000000").status_code == 404
