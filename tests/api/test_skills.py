from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from fastapi import FastAPI
from starlette.testclient import TestClient

NOW = datetime(2026, 1, 2, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


def _create_skill(client: TestClient, *, key: str = "research.roundtrip") -> dict[str, object]:
    response = client.post(
        "/v1/skills",
        json={"key": key, "display_name": "Roundtrip", "description": "desc"},
    )
    assert response.status_code == 201
    return response.json()


def _create_revision(
    client: TestClient, skill_id: str, *, instructions: str = "Repair tests"
) -> dict[str, object]:
    response = client.post(
        f"/v1/skills/{skill_id}/revisions",
        json={"instructions": instructions, "source_kind": "operator"},
    )
    assert response.status_code == 201
    return response.json()


def test_skill_revision_create_reload_roundtrip_preserves_content(app: FastAPI) -> None:
    app.state.clock = _Clock()
    with TestClient(app) as client:
        skill = _create_skill(client)
        instructions = "Fetch the latest plans and diff them against disk state."
        revision = _create_revision(client, str(skill["id"]), instructions=instructions)

        listed = client.get(f"/v1/skills/{skill['id']}/revisions")
        fetched = client.get(f"/v1/skills/{skill['id']}")

    assert listed.status_code == 200
    assert revision["instructions"] == instructions
    assert revision["content_sha256"] == hashlib.sha256(instructions.encode()).hexdigest()
    reloaded = listed.json()[0]
    assert reloaded["id"] == revision["id"]
    assert reloaded["instructions"] == instructions
    assert reloaded["content_sha256"] == revision["content_sha256"]
    assert fetched.json()["active_revision_id"] is None


def test_skill_lifecycle_activate_disable_archive(app: FastAPI) -> None:
    app.state.clock = _Clock()
    with TestClient(app) as client:
        skill = _create_skill(client, key="research.lifecycle")
        revision = _create_revision(client, str(skill["id"]))

        activated = client.post(f"/v1/skills/{skill['id']}/revisions/{revision['id']}/activate")
        disabled = client.post(f"/v1/skills/{skill['id']}/disable")
        archived = client.post(f"/v1/skills/{skill['id']}/archive")

    assert activated.status_code == 200
    assert activated.json()["active_revision_id"] == revision["id"]
    assert disabled.json()["status"] == "disabled"
    assert archived.json()["status"] == "archived"


def test_missing_skill_returns_404_across_read_and_write_routes(app: FastAPI) -> None:
    app.state.clock = _Clock()
    missing = "00000000-0000-0000-0000-0000000000ff"
    with TestClient(app) as client:
        get_response = client.get(f"/v1/skills/{missing}")
        create_revision = client.post(
            f"/v1/skills/{missing}/revisions",
            json={"instructions": "Nope", "source_kind": "operator"},
        )
        list_revisions = client.get(f"/v1/skills/{missing}/revisions")
        disable = client.post(f"/v1/skills/{missing}/disable")
        archive = client.post(f"/v1/skills/{missing}/archive")

    for response in (get_response, create_revision, list_revisions, disable, archive):
        assert response.status_code == 404
        assert response.json()["error"]["type"] == "skill_not_found"


def test_activate_missing_revision_returns_404(app: FastAPI) -> None:
    app.state.clock = _Clock()
    with TestClient(app) as client:
        skill = _create_skill(client)
        response = client.post(
            f"/v1/skills/{skill['id']}/revisions/00000000-0000-0000-0000-0000000000ff/activate"
        )

    assert response.status_code == 404
    assert response.json()["error"]["type"] == "skill_revision_not_found"


def test_cross_skill_activation_is_rejected(app: FastAPI) -> None:
    app.state.clock = _Clock()
    with TestClient(app) as client:
        a = _create_skill(client, key="research.a")
        b = _create_skill(client, key="research.b")
        revision = _create_revision(client, str(b["id"]))
        response = client.post(f"/v1/skills/{a['id']}/revisions/{revision['id']}/activate")

    assert response.status_code == 422
    assert response.json()["error"]["type"] == "validation_error"
