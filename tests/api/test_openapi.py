"""Phase 9 OpenAPI stability (spec section 16): generation succeeds,
operation IDs are unique, expected surface paths exist, no ORM leaks in."""

from __future__ import annotations

from typing import Any

from apps.api.app import create_app
from apps.api.settings import ApiSettings

EXPECTED_OPERATIONS = {
    ("GET", "/health", "getHealth"),
    ("GET", "/v1/tasks", "listTasks"),
    ("POST", "/v1/tasks", "createTask"),
    ("GET", "/v1/tasks/{task_id}", "getTask"),
    ("POST", "/v1/tasks/{task_id}/runs", "startRun"),
    ("GET", "/v1/tasks/{task_id}/runs", "listRunsForTask"),
    ("POST", "/v1/tasks/{task_id}/cancel", "cancelTask"),
    ("POST", "/v1/tasks/{task_id}/complete", "completeTask"),
    ("POST", "/v1/tasks/{task_id}/fail", "failTask"),
    ("GET", "/v1/tasks/{task_id}/events", "listTaskEvents"),
    ("GET", "/v1/runs/{run_id}", "getRun"),
    ("POST", "/v1/runs/{run_id}/start", "startQueuedRun"),
    ("POST", "/v1/runs/{run_id}/complete", "completeRun"),
    ("POST", "/v1/runs/{run_id}/fail", "failRun"),
    ("POST", "/v1/runs/{run_id}/cancel", "cancelRun"),
    ("POST", "/v1/runs/{run_id}/retry", "retryFailedRun"),
    ("GET", "/v1/runs/{run_id}/steps", "listRunStepsForRun"),
    ("POST", "/v1/runs/{run_id}/steps", "createOrderedStep"),
    ("GET", "/v1/runs/{run_id}/approvals", "listApprovalsForRun"),
    ("POST", "/v1/runs/{run_id}/approvals", "requestApproval"),
    ("GET", "/v1/runs/{run_id}/tool-invocations", "listToolInvocationsForRun"),
    ("POST", "/v1/runs/{run_id}/tool-invocations", "requestToolInvocation"),
    ("GET", "/v1/runs/{run_id}/artifacts", "listArtifactsForRun"),
    ("POST", "/v1/runs/{run_id}/artifacts", "recordArtifact"),
    ("GET", "/v1/runs/{run_id}/events", "listRunEvents"),
    ("GET", "/v1/runs/{run_id}/events/stream", "streamRunEvents"),
    ("GET", "/v1/steps/{step_id}", "getRunStep"),
    ("POST", "/v1/steps/{step_id}/start", "startStep"),
    ("POST", "/v1/steps/{step_id}/complete", "completeStep"),
    ("POST", "/v1/steps/{step_id}/fail", "failStep"),
    ("POST", "/v1/steps/{step_id}/skip", "skipPendingStep"),
    ("POST", "/v1/steps/{step_id}/cancel", "cancelStep"),
    ("GET", "/v1/steps/{step_id}/tool-invocations", "listToolInvocationsForStep"),
    ("GET", "/v1/approvals/{approval_id}", "getApproval"),
    ("POST", "/v1/approvals/{approval_id}/approve", "approveRequest"),
    ("POST", "/v1/approvals/{approval_id}/reject", "rejectRequest"),
    ("POST", "/v1/approvals/{approval_id}/cancel", "cancelApproval"),
    ("POST", "/v1/approvals/{approval_id}/expire", "expireApproval"),
    ("GET", "/v1/tool-invocations/{invocation_id}", "getToolInvocation"),
    ("POST", "/v1/tool-invocations/{invocation_id}/running", "markToolInvocationRunning"),
    ("POST", "/v1/tool-invocations/{invocation_id}/succeed", "markToolInvocationSucceeded"),
    ("POST", "/v1/tool-invocations/{invocation_id}/fail", "markToolInvocationFailed"),
    ("POST", "/v1/tool-invocations/{invocation_id}/cancel", "cancelToolInvocation"),
    ("GET", "/v1/artifacts/{artifact_id}", "getArtifact"),
}


def _schema() -> dict[str, Any]:
    settings = ApiSettings(
        database_url="sqlite://",
        host="127.0.0.1",
        port=8000,
        sse_poll_interval_seconds=0.1,
    )
    app = create_app(settings)
    try:
        return app.openapi()
    finally:
        app.state.engine.dispose()


def test_openapi_generation_succeeds_with_stable_title_and_version() -> None:
    schema = _schema()
    assert schema["info"]["title"] == "Friday Agent OS API"
    assert schema["info"]["version"] == "0.1.0"


def test_endpoint_matrix_is_exact_and_documents_errors() -> None:
    schema = _schema()
    actual = {
        (method.upper(), path, operation["operationId"])
        for path, methods in schema["paths"].items()
        for method, operation in methods.items()
        if method in {"get", "post", "put", "patch", "delete"}
    }
    assert actual == EXPECTED_OPERATIONS
    for _method, path, operation_id in EXPECTED_OPERATIONS:
        operation = schema["paths"][path][_method.lower()]
        assert operation["operationId"] == operation_id
        assert {"404", "409", "422", "500"} <= operation["responses"].keys()


def test_operation_ids_are_unique_and_present() -> None:
    schema = _schema()
    operation_ids = [
        details.get("operationId")
        for methods in schema["paths"].values()
        for details in methods.values()
    ]
    assert all(operation_ids)
    assert len(operation_ids) == len(set(operation_ids))


def test_start_run_response_uses_named_schema_not_raw_dict() -> None:
    schema = _schema()
    responses = schema["paths"]["/v1/tasks/{task_id}/runs"]["post"]["responses"]
    content = responses["201"]["content"]["application/json"]["schema"]
    assert content["$ref"] == "#/components/schemas/StartRunResponse"


def test_no_orm_row_type_names_leak_into_schema_components() -> None:
    schema = _schema()
    component_names = schema.get("components", {}).get("schemas", {}).keys()
    assert not any(name.endswith("Row") for name in component_names)


def test_openapi_schema_is_deterministic_across_generations() -> None:
    assert _schema() == _schema()
