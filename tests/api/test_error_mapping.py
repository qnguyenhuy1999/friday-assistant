"""Proves every stable ApplicationError subtype maps to its documented HTTP
status/body, and that SQLAlchemy/traceback details never reach the response.
Uses a throwaway route mounted on a real app instance -- exercising the same
`register_exception_handlers` wiring `create_app` uses -- rather than
duplicating the mapping logic in each business route's own tests."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from apps.api.errors import register_exception_handlers
from friday.application.errors import (
    ApplicationError,
    ApprovalNotFound,
    ArtifactNotFound,
    ConcurrencyConflict,
    EntityConflict,
    RunNotFound,
    RunStepNotFound,
    TaskNotFound,
    ToolInvocationNotFound,
    TransactionFailure,
)
from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import (
    ApprovalRequestId,
    ArtifactId,
    RunId,
    RunStepId,
    TaskId,
    ToolInvocationId,
)


def _client_raising(exc: ApplicationError) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise exc

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("exc", "expected_status", "expected_type"),
    [
        (TaskNotFound(TaskId.new()), 404, "task_not_found"),
        (RunNotFound(RunId.new()), 404, "run_not_found"),
        (RunStepNotFound(RunStepId.new()), 404, "run_step_not_found"),
        (ApprovalNotFound(ApprovalRequestId.new()), 404, "approval_not_found"),
        (ToolInvocationNotFound(ToolInvocationId.new()), 404, "tool_invocation_not_found"),
        (ArtifactNotFound(ArtifactId.new()), 404, "artifact_not_found"),
        (EntityConflict("conflict"), 409, "entity_conflict"),
        (ConcurrencyConflict("stale"), 409, "concurrency_conflict"),
        (TransactionFailure("db down"), 500, "transaction_failure"),
    ],
)
def test_application_error_maps_to_documented_response(
    exc: ApplicationError, expected_status: int, expected_type: str
) -> None:
    response = _client_raising(exc).get("/boom")
    assert response.status_code == expected_status
    body = response.json()
    assert body["error"]["type"] == expected_type
    assert "traceback" not in body["error"]
    assert "sqlalchemy" not in body["error"]["message"].lower()


def test_transaction_failure_message_is_generic_not_the_raw_exception_text() -> None:
    response = _client_raising(TransactionFailure("IntegrityError: secret/path/leak")).get("/boom")
    assert "secret/path/leak" not in response.json()["error"]["message"]


def test_validation_error_maps_to_422() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/items/{item_id}")
    def get_item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/items/not-an-int")

    assert response.status_code == 422
    assert response.json()["error"]["type"] == "validation_error"


def test_domain_validation_error_maps_to_422() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom-domain")
    def boom_domain() -> None:
        raise DomainValidationError("internal details")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/boom-domain")

    assert response.status_code == 422
    assert response.json()["error"]["type"] == "validation_error"
    assert "internal details" not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/v1/approvals/not-a-uuid",
        "/v1/artifacts/not-a-uuid",
        "/v1/tool-invocations/not-a-uuid",
        "/v1/runs/not-a-uuid/events",
    ],
)
def test_invalid_id_maps_to_stable_422_schema_across_endpoints(
    client: TestClient, path: str
) -> None:
    response = client.get(path)
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "validation_error"
