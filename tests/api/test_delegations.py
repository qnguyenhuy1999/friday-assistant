from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from fastapi import FastAPI
from starlette.testclient import TestClient

from tests.api.conftest import seed_active_run

NOW = datetime(2026, 1, 2, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


def _create_active_agent(client: TestClient, *, key: str = "research.delegate-target") -> str:
    created = client.post(
        "/v1/agents", json={"key": key, "display_name": "Target", "description": ""}
    )
    assert created.status_code == 201
    agent_id = cast(str, created.json()["id"])
    revision = client.post(
        f"/v1/agents/{agent_id}/revisions",
        json={
            "instructions": "be helpful",
            "runtime_kind": "claude_cli",
            "runtime_config": {},
            "source_kind": "operator",
        },
    )
    assert revision.status_code == 201
    activate = client.post(f"/v1/agents/{agent_id}/revisions/{revision.json()['id']}/activate")
    assert activate.status_code == 200
    return agent_id


def test_run_agent_endpoint_reports_unresolved_by_default(app: FastAPI) -> None:
    seeded = seed_active_run(app)
    with TestClient(app) as client:
        response = client.get(f"/v1/runs/{seeded.run_id}/agent")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "run_id": str(seeded.run_id),
        "resolved": False,
        "resolved_at": None,
        "agent_id": None,
        "revision_id": None,
    }


def test_run_agent_endpoint_404s_for_missing_run(app: FastAPI) -> None:
    with TestClient(app) as client:
        response = client.get("/v1/runs/00000000-0000-0000-0000-000000000000/agent")
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "run_not_found"


def test_create_and_list_and_get_delegation_request(app: FastAPI) -> None:
    app.state.clock = _Clock()
    seeded = seed_active_run(app)
    with TestClient(app) as client:
        agent_id = _create_active_agent(client)

        created = client.post(
            f"/v1/runs/{seeded.run_id}/delegations",
            json={
                "target_agent_id": agent_id,
                "objective": "summarize recent errors",
                "input_payload": {"log_ids": ["a", "b"]},
                "expected_output_contract": "json object with a summary field",
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["parent_run_id"] == str(seeded.run_id)
        assert body["target_agent_id"] == agent_id
        assert body["status"] == "requested"
        assert len(body["authorization_fingerprint"]) == 64

        listed = client.get(f"/v1/runs/{seeded.run_id}/delegations")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [body["id"]]

        fetched = client.get(f"/v1/delegations/{body['id']}")
        assert fetched.status_code == 200
        assert fetched.json() == body


def test_create_delegation_request_404s_for_missing_target_agent(app: FastAPI) -> None:
    seeded = seed_active_run(app)
    with TestClient(app) as client:
        response = client.post(
            f"/v1/runs/{seeded.run_id}/delegations",
            json={
                "target_agent_id": "00000000-0000-0000-0000-000000000000",
                "objective": "do x",
                "expected_output_contract": "y",
            },
        )
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "agent_not_found"


def test_get_missing_delegation_request_returns_404(app: FastAPI) -> None:
    with TestClient(app) as client:
        response = client.get("/v1/delegations/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "delegation_request_not_found"
