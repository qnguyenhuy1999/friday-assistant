from __future__ import annotations

from typing import cast

from starlette.testclient import TestClient


def _task(client: TestClient) -> str:
    response = client.post("/v1/tasks", json={"title": "Workflow task", "description": ""})
    assert response.status_code == 201
    return str(response.json()["id"])


def _workflow(client: TestClient, *, key: str = "api.workflow") -> str:
    response = client.post(
        "/v1/workflows",
        json={"key": key, "display_name": "API workflow", "description": ""},
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _agent(client: TestClient, *, key: str = "api.workflow.agent") -> dict[str, str]:
    response = client.post(
        "/v1/agents",
        json={"key": key, "display_name": "API agent", "description": ""},
    )
    assert response.status_code == 201
    return cast(dict[str, str], response.json())


def test_task_workflow_binding_put_get_delete_round_trip(client: TestClient) -> None:
    task_id = _task(client)
    workflow_id = _workflow(client)

    assert client.get(f"/v1/tasks/{task_id}/workflow").json() is None
    bound = client.put(f"/v1/tasks/{task_id}/workflow", json={"workflow_id": workflow_id})
    assert bound.status_code == 200
    assert bound.json()["task_id"] == task_id
    assert bound.json()["workflow_id"] == workflow_id

    fetched = client.get(f"/v1/tasks/{task_id}/workflow")
    assert fetched.status_code == 200
    assert fetched.json() == bound.json()

    deleted = client.delete(f"/v1/tasks/{task_id}/workflow")
    assert deleted.status_code == 204
    assert client.get(f"/v1/tasks/{task_id}/workflow").json() is None


def test_task_workflow_binding_is_mutually_exclusive_with_task_agent_binding(
    client: TestClient,
) -> None:
    agent = _agent(client)
    revision = client.post(
        f"/v1/agents/{agent['id']}/revisions",
        json={
            "instructions": "work",
            "runtime_kind": "claude_cli",
            "runtime_config": {},
            "source_kind": "operator",
        },
    ).json()
    assert (
        client.post(f"/v1/agents/{agent['id']}/revisions/{revision['id']}/activate").status_code
        == 200
    )

    task_id = _task(client)
    workflow_id = _workflow(client, key="api.workflow.exclusive")
    assert (
        client.put(f"/v1/tasks/{task_id}/agent", json={"agent_id": agent["id"]}).status_code == 200
    )

    rejected_workflow = client.put(
        f"/v1/tasks/{task_id}/workflow", json={"workflow_id": workflow_id}
    )
    assert rejected_workflow.status_code == 409
    assert rejected_workflow.json()["error"]["type"] == "workflow_binding_error"

    second_task_id = _task(client)
    assert (
        client.put(
            f"/v1/tasks/{second_task_id}/workflow", json={"workflow_id": workflow_id}
        ).status_code
        == 200
    )
    rejected_agent = client.put(f"/v1/tasks/{second_task_id}/agent", json={"agent_id": agent["id"]})
    assert rejected_agent.status_code == 409
    assert rejected_agent.json()["error"]["type"] == "workflow_binding_error"


def test_archived_workflow_cannot_be_newly_bound(client: TestClient) -> None:
    task_id = _task(client)
    workflow_id = _workflow(client, key="api.workflow.archived")
    assert client.post(f"/v1/workflows/{workflow_id}/archive").status_code == 200

    rejected = client.put(f"/v1/tasks/{task_id}/workflow", json={"workflow_id": workflow_id})
    assert rejected.status_code == 409
    assert rejected.json()["error"]["type"] == "workflow_binding_error"
