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


def test_latest_in_execution_follows_retry_chain(client: TestClient) -> None:
    task = client.post("/v1/tasks", json={"title": "Ship"}).json()
    started = client.post(f"/v1/tasks/{task['id']}/runs")
    run_id = started.json()["run_id"]
    client.post(f"/v1/runs/{run_id}/start")
    failure = {
        "code": "x",
        "message": "boom",
        "retryable": False,
        "cause": "internal",
        "details": None,
    }
    client.post(f"/v1/runs/{run_id}/fail", json=failure)
    unchanged = client.get(f"/v1/runs/{run_id}/latest-in-execution").json()
    assert unchanged["id"] == run_id
    retried = client.post(f"/v1/runs/{run_id}/retry").json()
    latest = client.get(f"/v1/runs/{run_id}/latest-in-execution").json()
    assert latest["id"] == retried["id"]
    assert latest["id"] != run_id
    missing = client.get("/v1/runs/00000000-0000-0000-0000-000000000000/latest-in-execution")
    assert missing.status_code == 404
