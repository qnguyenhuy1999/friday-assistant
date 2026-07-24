from __future__ import annotations

from starlette.testclient import TestClient


def test_task_cursor_pagination_and_validation(client: TestClient) -> None:
    ids = [
        client.post("/v1/tasks", json={"title": f"Task {number}"}).json()["id"]
        for number in range(3)
    ]
    first = client.get("/v1/tasks?limit=2")
    assert first.status_code == 200
    page = first.json()
    assert [item["id"] for item in page["items"]] == ids[:2]
    second = client.get(f"/v1/tasks?limit=2&cursor={page['next_cursor']}").json()
    assert [item["id"] for item in second["items"]] == ids[2:]
    assert second["next_cursor"] is None
    assert client.get("/v1/tasks?limit=101").status_code == 422
    assert client.get("/v1/tasks?cursor=bad").status_code == 422


def test_run_and_step_cursor_pagination(client: TestClient) -> None:
    task_id = client.post("/v1/tasks", json={"title": "Task"}).json()["id"]
    run_ids = [client.post(f"/v1/tasks/{task_id}/runs").json()["run_id"]]
    client.post(f"/v1/runs/{run_ids[0]}/cancel")
    run_ids.append(client.post(f"/v1/tasks/{task_id}/runs").json()["run_id"])
    runs = client.get(f"/v1/tasks/{task_id}/runs?limit=1").json()
    assert [item["id"] for item in runs["items"]] == run_ids[:1]
    assert (
        client.get(f"/v1/tasks/{task_id}/runs?limit=1&cursor={runs['next_cursor']}").json()[
            "items"
        ][0]["id"]
        == run_ids[1]
    )

    client.post(f"/v1/runs/{run_ids[1]}/start")
    for name in ("one", "two", "three"):
        client.post(f"/v1/runs/{run_ids[1]}/steps", json={"name": name})
    first_steps = client.get(f"/v1/runs/{run_ids[1]}/steps?limit=2").json()
    remaining_steps = client.get(
        f"/v1/runs/{run_ids[1]}/steps?limit=2&cursor={first_steps['next_cursor']}"
    ).json()
    positions = [item["position"] for item in first_steps["items"] + remaining_steps["items"]]
    assert positions == [0, 1, 2]
    assert client.get(f"/v1/runs/{run_ids[1]}/steps?cursor=bad").status_code == 422


def test_run_events_cursor_cannot_be_reused_across_runs(client: TestClient) -> None:
    task_id = client.post("/v1/tasks", json={"title": "Task"}).json()["id"]
    run_a = client.post(f"/v1/tasks/{task_id}/runs").json()["run_id"]
    run_b = client.post(f"/v1/tasks/{task_id}/runs").json()["run_id"]
    client.post(f"/v1/runs/{run_a}/start")
    client.post(f"/v1/runs/{run_a}/complete")

    run_a_cursor = client.get(f"/v1/runs/{run_a}/events?limit=1").json()["next_cursor"]
    assert run_a_cursor is not None
    assert client.get(f"/v1/runs/{run_b}/events?cursor={run_a_cursor}").status_code == 422


def test_task_list_cursor_cannot_be_reused_for_task_runs(client: TestClient) -> None:
    client.post("/v1/tasks", json={"title": "Task 1"})
    client.post("/v1/tasks", json={"title": "Task 2"})
    task_id = client.post("/v1/tasks", json={"title": "Task 3"}).json()["id"]
    client.post(f"/v1/tasks/{task_id}/runs")
    client.post(f"/v1/tasks/{task_id}/runs")

    tasks_cursor = client.get("/v1/tasks?limit=1").json()["next_cursor"]
    assert tasks_cursor is not None
    assert client.get(f"/v1/tasks/{task_id}/runs?cursor={tasks_cursor}").status_code == 422
