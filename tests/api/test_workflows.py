from __future__ import annotations

from starlette.testclient import TestClient


def test_workflow_revision_and_lifecycle_endpoints(client: TestClient) -> None:
    agent = client.post(
        "/v1/agents",
        json={"key": "workflow.target", "display_name": "Target", "description": ""},
    ).json()
    workflow = client.post(
        "/v1/workflows",
        json={"key": "workflow.registry", "display_name": "Registry"},
    ).json()
    revision_response = client.post(
        f"/v1/workflows/{workflow['id']}/revisions",
        json={
            "nodes": [
                {
                    "node_key": "start",
                    "target_agent_id": agent["id"],
                    "objective": "start",
                    "input_payload": {},
                    "expected_output_contract": "done",
                },
                {
                    "node_key": "finish",
                    "target_agent_id": agent["id"],
                    "objective": "finish",
                    "input_payload": {},
                    "expected_output_contract": "done",
                },
            ],
            "edges": [{"from": "start", "to": "finish"}],
            "source_kind": "operator",
        },
    )
    assert revision_response.status_code == 201
    revision = revision_response.json()
    assert revision["version"] == 1
    assert [node["node_key"] for node in revision["nodes"]] == ["finish", "start"]
    assert revision["edges"][0]["from"] == "start"

    listed = client.get(f"/v1/workflows/{workflow['id']}/revisions")
    fetched = client.get(f"/v1/workflows/{workflow['id']}/revisions/{revision['id']}")
    activated = client.post(f"/v1/workflows/{workflow['id']}/revisions/{revision['id']}/activate")
    disabled = client.post(f"/v1/workflows/{workflow['id']}/disable")
    archived = client.post(f"/v1/workflows/{workflow['id']}/archive")

    assert listed.status_code == fetched.status_code == 200
    assert listed.json()[0]["id"] == fetched.json()["id"] == revision["id"]
    assert activated.json()["active_revision_id"] == revision["id"]
    assert disabled.json()["status"] == "disabled"
    assert archived.json()["status"] == "archived"
