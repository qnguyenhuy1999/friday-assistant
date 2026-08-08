from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from fastapi import FastAPI
from starlette.testclient import TestClient

NOW = datetime(2026, 1, 2, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


def _create_agent(client: TestClient, *, key: str = "research.coder") -> dict[str, object]:
    response = client.post(
        "/v1/agents",
        json={"key": key, "display_name": "Coder", "description": "desc"},
    )
    assert response.status_code == 201
    return cast(dict[str, object], response.json())


def _create_revision(
    client: TestClient, agent_id: str, *, instructions: str = "be helpful"
) -> dict[str, object]:
    response = client.post(
        f"/v1/agents/{agent_id}/revisions",
        json={
            "instructions": instructions,
            "runtime_kind": "claude_cli",
            "runtime_config": {},
            "source_kind": "operator",
        },
    )
    assert response.status_code == 201
    return cast(dict[str, object], response.json())


def test_agent_revision_create_reload_roundtrip_preserves_content(app: FastAPI) -> None:
    app.state.clock = _Clock()
    with TestClient(app) as client:
        agent = _create_agent(client)
        instructions = "Read the task context, then propose exactly one bounded action."
        revision = _create_revision(client, str(agent["id"]), instructions=instructions)

        listed = client.get(f"/v1/agents/{agent['id']}/revisions")
        fetched = client.get(f"/v1/agents/{agent['id']}")

    assert listed.status_code == 200
    assert revision["instructions"] == instructions
    reloaded = listed.json()[0]
    assert reloaded["id"] == revision["id"]
    assert reloaded["instructions"] == instructions
    assert reloaded["content_sha256"] == revision["content_sha256"]
    assert fetched.json()["active_revision_id"] is None


def test_agent_lifecycle_activate_disable_archive(app: FastAPI) -> None:
    app.state.clock = _Clock()
    with TestClient(app) as client:
        agent = _create_agent(client, key="research.lifecycle")
        revision = _create_revision(client, str(agent["id"]))

        activated = client.post(f"/v1/agents/{agent['id']}/revisions/{revision['id']}/activate")
        disabled = client.post(f"/v1/agents/{agent['id']}/disable")
        archived = client.post(f"/v1/agents/{agent['id']}/archive")

    assert activated.status_code == 200
    assert activated.json()["active_revision_id"] == revision["id"]
    assert disabled.json()["status"] == "disabled"
    assert archived.json()["status"] == "archived"


def test_create_agent_revision_rejects_unknown_runtime_kind(app: FastAPI) -> None:
    app.state.clock = _Clock()
    with TestClient(app) as client:
        agent = _create_agent(client, key="research.unknown-runtime")
        response = client.post(
            f"/v1/agents/{agent['id']}/revisions",
            json={
                "instructions": "x",
                "runtime_kind": "totally_unknown",
                "runtime_config": {},
                "source_kind": "operator",
            },
        )
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "unknown_brain_runtime_kind"


def test_get_missing_agent_returns_404(app: FastAPI) -> None:
    with TestClient(app) as client:
        response = client.get("/v1/agents/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "agent_not_found"


def test_task_agent_binding_get_and_put_roundtrip(app: FastAPI) -> None:
    app.state.clock = _Clock()
    with TestClient(app) as client:
        agent = _create_agent(client, key="research.binding")
        revision = _create_revision(client, str(agent["id"]))
        client.post(f"/v1/agents/{agent['id']}/revisions/{revision['id']}/activate")

        task = client.post("/v1/tasks", json={"title": "T", "description": ""})
        assert task.status_code == 201
        task_id = task.json()["id"]

        empty = client.get(f"/v1/tasks/{task_id}/agent")
        assert empty.status_code == 200 and empty.json() is None

        bound = client.put(f"/v1/tasks/{task_id}/agent", json={"agent_id": agent["id"]})
        assert bound.status_code == 200
        assert bound.json()["agent_id"] == agent["id"]

        fetched = client.get(f"/v1/tasks/{task_id}/agent")
        assert fetched.json()["agent_id"] == agent["id"]

        cleared = client.put(f"/v1/tasks/{task_id}/agent", json={"agent_id": None})
        assert cleared.status_code == 200 and cleared.json() is None
