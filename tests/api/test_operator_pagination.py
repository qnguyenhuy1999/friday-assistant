from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient


def test_agent_registry_paginates_beyond_the_first_page(app: FastAPI) -> None:
    with TestClient(app) as client:
        for index in range(3):
            created = client.post(
                "/v1/agents",
                json={
                    "key": f"pagination.agent-{index}",
                    "display_name": f"Agent {index}",
                    "description": "",
                },
            )
            assert created.status_code == 201

        first = client.get("/v1/agents?limit=2")
        assert first.status_code == 200
        first_body = first.json()
        assert len(first_body["items"]) == 2
        assert first_body["next_cursor"] is not None

        second = client.get(
            "/v1/agents",
            params={"limit": 2, "cursor": first_body["next_cursor"]},
        )
        assert second.status_code == 200
        second_body = second.json()
        assert len(second_body["items"]) == 1
        assert second_body["next_cursor"] is None
        ids = {item["id"] for item in first_body["items"] + second_body["items"]}
        assert len(ids) == 3


def test_workflow_revision_history_supports_bounded_newest_first_pages(
    app: FastAPI,
) -> None:
    with TestClient(app) as client:
        agent = client.post(
            "/v1/agents",
            json={
                "key": "pagination.workflow-agent",
                "display_name": "Workflow Agent",
                "description": "",
            },
        ).json()
        workflow = client.post(
            "/v1/workflows",
            json={
                "key": "pagination.workflow",
                "display_name": "Paginated Workflow",
            },
        ).json()

        for index in range(3):
            response = client.post(
                f"/v1/workflows/{workflow['id']}/revisions",
                json={
                    "nodes": [
                        {
                            "node_key": "root",
                            "target_agent_id": agent["id"],
                            "objective": f"revision {index + 1}",
                            "input_payload": {},
                            "expected_output_contract": "done",
                        }
                    ],
                    "edges": [],
                    "source_kind": "operator",
                },
            )
            assert response.status_code == 201

        first = client.get(
            f"/v1/workflows/{workflow['id']}/revisions",
            params={"limit": 2},
        )
        assert first.status_code == 200
        assert [item["version"] for item in first.json()] == [3, 2]

        second = client.get(
            f"/v1/workflows/{workflow['id']}/revisions",
            params={"limit": 2, "before_version": 2},
        )
        assert second.status_code == 200
        assert [item["version"] for item in second.json()] == [1]

        legacy = client.get(f"/v1/workflows/{workflow['id']}/revisions")
        assert legacy.status_code == 200
        assert [item["version"] for item in legacy.json()] == [1, 2, 3]
