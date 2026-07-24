"""Phase 9 OpenAPI stability (spec section 16): generation succeeds,
operation IDs are unique, expected surface paths exist, no ORM leaks in."""

from __future__ import annotations

from typing import Any

from apps.api.app import create_app
from apps.api.settings import ApiSettings

EXPECTED_PATHS = {
    "/health",
    "/v1/tasks",
    "/v1/tasks/{task_id}",
    "/v1/tasks/{task_id}/runs",
    "/v1/tasks/{task_id}/cancel",
    "/v1/tasks/{task_id}/complete",
    "/v1/tasks/{task_id}/fail",
    "/v1/tasks/{task_id}/events",
    "/v1/runs/{run_id}",
    "/v1/runs/{run_id}/start",
    "/v1/runs/{run_id}/complete",
    "/v1/runs/{run_id}/fail",
    "/v1/runs/{run_id}/cancel",
    "/v1/runs/{run_id}/retry",
    "/v1/runs/{run_id}/steps",
    "/v1/runs/{run_id}/approvals",
    "/v1/runs/{run_id}/tool-invocations",
    "/v1/runs/{run_id}/artifacts",
    "/v1/runs/{run_id}/events",
    "/v1/runs/{run_id}/events/stream",
    "/v1/steps/{step_id}",
    "/v1/approvals/{approval_id}",
    "/v1/tool-invocations/{invocation_id}",
    "/v1/artifacts/{artifact_id}",
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


def test_expected_paths_are_present() -> None:
    schema = _schema()
    missing = EXPECTED_PATHS - schema["paths"].keys()
    assert missing == set()


def test_operation_ids_are_unique_and_present() -> None:
    schema = _schema()
    operation_ids = [
        details.get("operationId")
        for methods in schema["paths"].values()
        for details in methods.values()
    ]
    assert all(operation_ids)
    assert len(operation_ids) == len(set(operation_ids))


def test_no_orm_row_type_names_leak_into_schema_components() -> None:
    schema = _schema()
    component_names = schema.get("components", {}).get("schemas", {}).keys()
    assert not any(name.endswith("Row") for name in component_names)


def test_openapi_schema_is_deterministic_across_generations() -> None:
    assert _schema() == _schema()
