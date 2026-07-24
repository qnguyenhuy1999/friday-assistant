from __future__ import annotations

from starlette.testclient import TestClient


def test_run_start_and_lifecycle(client: TestClient) -> None:
    task = client.post("/v1/tasks", json={"title": "Ship"}).json()
    started = client.post(f"/v1/tasks/{task['id']}/runs")
    assert started.status_code == 201
    run_id = started.json()["run_id"]
    assert client.get(f"/v1/runs/{run_id}").json()["status"] == "queued"
    assert client.post(f"/v1/runs/{run_id}/start").json()["status"] == "running"
    assert client.post(f"/v1/runs/{run_id}/complete").json()["status"] == "succeeded"
    assert client.post(f"/v1/runs/{run_id}/start").status_code == 409
    assert client.get("/v1/runs/00000000-0000-0000-0000-000000000000").status_code == 404
