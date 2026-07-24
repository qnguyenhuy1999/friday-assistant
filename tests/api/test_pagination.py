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
