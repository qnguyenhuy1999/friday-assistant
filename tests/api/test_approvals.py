from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from starlette.testclient import TestClient

from tests.api.conftest import seed_active_run


def _request(client: TestClient, run_id: str, *, expires_at: str | None = None) -> str:
    body: dict[str, object] = {
        "category": "tool_execution",
        "summary": "Deploy change",
        "reason": "Production action",
        "requested_action": "deploy",
        "requested_input": {"region": "us-east-1"},
    }
    if expires_at is not None:
        body["expires_at"] = expires_at
    response = client.post(
        f"/v1/runs/{run_id}/approvals",
        json=body,
    )
    assert response.status_code == 201
    assert response.json()["status"] == "pending"
    return str(response.json()["approval_id"])


def test_request_get_list_and_approve_an_approval(app: FastAPI) -> None:
    seeded = seed_active_run(app)
    with TestClient(app) as client:
        approval_id = _request(client, str(seeded.run_id))
        fetched = client.get(f"/v1/approvals/{approval_id}")
        listed = client.get(f"/v1/runs/{seeded.run_id}/approvals")
        approved = client.post(
            f"/v1/approvals/{approval_id}/approve",
            json={"resolver": "operator", "resolution_note": "approved"},
        )
    assert fetched.status_code == 200
    assert fetched.json()["requested_input"] == {"region": "us-east-1"}
    assert [item["approval_id"] for item in listed.json()["items"]] == [approval_id]
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"


def test_reject_cancel_and_expire_are_explicit_transitions(app: FastAPI) -> None:
    seeded = seed_active_run(app)
    with TestClient(app) as client:
        rejected_id = _request(client, str(seeded.run_id))
        rejected = client.post(f"/v1/approvals/{rejected_id}/reject", json={"resolver": "operator"})
    assert rejected.json()["status"] == "rejected"

    seeded = seed_active_run(app)
    with TestClient(app) as client:
        cancelled_id = _request(client, str(seeded.run_id))
        cancelled = client.post(
            f"/v1/approvals/{cancelled_id}/cancel", json={"resolution_note": "not needed"}
        )
    assert cancelled.json()["status"] == "cancelled"

    seeded = seed_active_run(app)
    with TestClient(app) as client:
        expired_id = _request(client, str(seeded.run_id), expires_at="2100-01-01T00:00:00Z")
        app.state.clock = _StaticClock(datetime(2101, 1, 1, tzinfo=UTC))
        expired = client.post(f"/v1/approvals/{expired_id}/expire")
    assert expired.json()["status"] == "expired"


def test_approval_errors_are_mapped_to_404_409_and_422(app: FastAPI) -> None:
    seeded = seed_active_run(app)
    with TestClient(app) as client:
        missing = client.get("/v1/approvals/00000000-0000-4000-8000-000000000099")
        invalid = client.post(
            f"/v1/runs/{seeded.run_id}/approvals", json={"category": "tool_execution"}
        )
        approval_id = _request(client, str(seeded.run_id))
        approved = client.post(
            f"/v1/approvals/{approval_id}/approve", json={"resolver": "operator"}
        )
        conflict = client.post(f"/v1/approvals/{approval_id}/reject", json={"resolver": "operator"})
    assert missing.status_code == 404
    assert invalid.status_code == 422
    assert approved.status_code == 200
    assert conflict.status_code == 409


class _StaticClock:
    def __init__(self, value: datetime) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value
