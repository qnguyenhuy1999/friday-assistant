from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient

from tests.api.conftest import seed_active_run


def _body(artifact_id: str | None = None) -> dict[str, object]:
    body: dict[str, object] = {
        "kind": "file",
        "name": "report.txt",
        "media_type": "text/plain",
        "location": "memory://reports/report.txt",
        "metadata": {"source": "test"},
    }
    if artifact_id is not None:
        body["artifact_id"] = artifact_id
    return body


def test_record_get_list_and_idempotent_replay(app: FastAPI) -> None:
    seeded = seed_active_run(app)
    supplied_id = "00000000-0000-4000-8000-000000000001"
    with TestClient(app) as client:
        recorded = client.post(f"/v1/runs/{seeded.run_id}/artifacts", json=_body(supplied_id))
        replay = client.post(f"/v1/runs/{seeded.run_id}/artifacts", json=_body(supplied_id))
        fetched = client.get(f"/v1/artifacts/{supplied_id}")
        listed = client.get(f"/v1/runs/{seeded.run_id}/artifacts")
    assert recorded.status_code == 201
    assert replay.status_code == 201
    assert replay.json() == recorded.json()
    assert fetched.json()["location"] == "memory://reports/report.txt"
    assert [item["artifact_id"] for item in listed.json()["items"]] == [supplied_id]


def test_artifact_errors_are_mapped_to_404_409_and_422(app: FastAPI) -> None:
    seeded = seed_active_run(app)
    supplied_id = "00000000-0000-4000-8000-000000000002"
    changed = _body(supplied_id)
    changed["location"] = "memory://different"
    with TestClient(app) as client:
        missing = client.get("/v1/artifacts/00000000-0000-4000-8000-000000000099")
        invalid = client.post(f"/v1/runs/{seeded.run_id}/artifacts", json={"kind": "file"})
        created = client.post(f"/v1/runs/{seeded.run_id}/artifacts", json=_body(supplied_id))
        conflict = client.post(f"/v1/runs/{seeded.run_id}/artifacts", json=changed)
    assert missing.status_code == 404
    assert invalid.status_code == 422
    assert created.status_code == 201
    assert conflict.status_code == 409
