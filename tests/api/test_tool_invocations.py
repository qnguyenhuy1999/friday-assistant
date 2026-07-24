from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient

from tests.api.conftest import seed_active_run


def _request(client: TestClient, run_id: str) -> str:
    response = client.post(
        f"/v1/runs/{run_id}/tool-invocations",
        json={"tool_name": "metadata-only", "requested_input": {"command": "never-run"}},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "requested"
    return str(response.json()["invocation_id"])


def test_request_get_list_and_succeed_are_metadata_only(app: FastAPI) -> None:
    seeded = seed_active_run(app)
    with TestClient(app) as client:
        invocation_id = _request(client, str(seeded.run_id))
        fetched = client.get(f"/v1/tool-invocations/{invocation_id}")
        listed = client.get(f"/v1/runs/{seeded.run_id}/tool-invocations")
        running = client.post(f"/v1/tool-invocations/{invocation_id}/running")
        succeeded = client.post(
            f"/v1/tool-invocations/{invocation_id}/succeed", json={"output": {"ok": True}}
        )
    assert fetched.status_code == 200
    assert listed.json()["items"][0]["invocation_id"] == invocation_id
    assert running.json()["status"] == "running"
    assert succeeded.json()["status"] == "succeeded"
    # The API only records supplied input/output; it has no executor side effect.
    assert succeeded.json()["output"] == {"ok": True}


def test_fail_cancel_and_error_responses(app: FastAPI) -> None:
    seeded = seed_active_run(app)
    with TestClient(app) as client:
        failed_id = _request(client, str(seeded.run_id))
        client.post(f"/v1/tool-invocations/{failed_id}/running")
        failed = client.post(
            f"/v1/tool-invocations/{failed_id}/fail",
            json={
                "failure": {
                    "code": "timeout",
                    "message": "timed out",
                    "retryable": True,
                    "cause": "tool",
                    "details": {"attempt": 1},
                }
            },
        )
    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"

    seeded = seed_active_run(app)
    with TestClient(app) as client:
        cancelled_id = _request(client, str(seeded.run_id))
        cancelled = client.post(f"/v1/tool-invocations/{cancelled_id}/cancel")
        invalid = client.post(f"/v1/runs/{seeded.run_id}/tool-invocations", json={})
        conflict = client.post(f"/v1/tool-invocations/{cancelled_id}/running")
    assert cancelled.json()["status"] == "cancelled"
    assert invalid.status_code == 422
    assert conflict.status_code == 409


def test_tool_invocation_not_found_is_404(app: FastAPI) -> None:
    with TestClient(app) as client:
        response = client.get("/v1/tool-invocations/00000000-0000-4000-8000-000000000000")
    assert response.status_code == 404
