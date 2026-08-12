"""Phase 9 OpenAPI stability (spec section 16): generation succeeds,
operation IDs are unique, expected surface paths exist, no ORM leaks in."""

# ruff: noqa: E501

from __future__ import annotations

from typing import Any

from apps.api.app import create_app
from apps.api.settings import ApiSettings

EXPECTED_OPERATIONS = {
    ("GET", "/health", "getHealth"),
    ("GET", "/ready", "getReadiness"),
    ("POST", "/v1/conversations", "createConversation"),
    ("GET", "/v1/conversations/{conversation_id}", "getConversation"),
    ("GET", "/v1/conversations/{conversation_id}/turns", "listConversationTurns"),
    ("POST", "/v1/conversations/{conversation_id}/turns", "submitConversationTurn"),
    ("GET", "/v1/tasks", "listTasks"),
    ("POST", "/v1/tasks", "createTask"),
    ("POST", "/v1/skills", "createSkill"),
    ("GET", "/v1/skills", "listSkills"),
    ("GET", "/v1/skills/{skill_id}", "getSkill"),
    ("POST", "/v1/skills/{skill_id}/revisions", "createSkillRevision"),
    ("GET", "/v1/skills/{skill_id}/revisions", "listSkillRevisions"),
    ("POST", "/v1/skills/{skill_id}/revisions/{revision_id}/activate", "activateSkillRevision"),
    ("POST", "/v1/skills/{skill_id}/disable", "disableSkill"),
    ("POST", "/v1/skills/{skill_id}/archive", "archiveSkill"),
    ("GET", "/v1/tasks/{task_id}/skills", "getTaskSkills"),
    ("PUT", "/v1/tasks/{task_id}/skills", "replaceTaskSkills"),
    ("GET", "/v1/runs/{run_id}/skills", "getRunSkillsAudit"),
    (
        "POST",
        "/v1/runs/{run_id}/skills/{skill_id}/feedback",
        "addRunSkillFeedback",
    ),
    (
        "GET",
        "/v1/runs/{run_id}/skills/{skill_id}/feedback",
        "listRunSkillFeedback",
    ),
    ("GET", "/v1/skills/{skill_id}/usage", "listSkillUsage"),
    ("GET", "/v1/skills/evidence-snapshots/{snapshot_id}", "getSkillEvidenceSnapshot"),
    (
        "GET",
        "/v1/skills/{skill_id}/improvement-proposals",
        "listSkillImprovementProposals",
    ),
    (
        "GET",
        "/v1/skills/improvement-proposals/{proposal_id}",
        "getSkillImprovementProposal",
    ),
    (
        "POST",
        "/v1/skills/improvement-proposals/{proposal_id}/cancel",
        "cancelSkillImprovementProposal",
    ),
    (
        "POST",
        "/v1/skills/improvement-proposals/{proposal_id}/evaluate",
        "evaluateSkillImprovementProposal",
    ),
    (
        "GET",
        "/v1/skills/improvement-proposals/{proposal_id}/evaluation",
        "getSkillImprovementEvaluation",
    ),
    (
        "POST",
        "/v1/skills/improvement-proposals/{proposal_id}/request-promotion",
        "requestSkillPromotion",
    ),
    (
        "GET",
        "/v1/skills/promotions/{promotion_id}",
        "getSkillPromotionRequest",
    ),
    (
        "POST",
        "/v1/skills/promotions/{promotion_id}/approve",
        "executeSkillPromotion",
    ),
    (
        "POST",
        "/v1/skills/promotions/{promotion_id}/reject",
        "rejectSkillPromotion",
    ),
    (
        "POST",
        "/v1/skills/promotions/{promotion_id}/cancel",
        "cancelSkillPromotion",
    ),
    (
        "POST",
        "/v1/skills/{skill_id}/request-rollback",
        "requestSkillRollback",
    ),
    (
        "POST",
        "/v1/skills/rollbacks/{rollback_id}/approve",
        "executeSkillRollback",
    ),
    (
        "GET",
        "/v1/skills/rollbacks/{rollback_id}",
        "getSkillRollbackRequest",
    ),
    (
        "POST",
        "/v1/skills/rollbacks/{rollback_id}/reject",
        "rejectSkillRollback",
    ),
    (
        "POST",
        "/v1/skills/rollbacks/{rollback_id}/cancel",
        "cancelSkillRollback",
    ),
    (
        "GET",
        "/v1/skills/{skill_id}/improvement-policy",
        "getSkillImprovementPolicy",
    ),
    (
        "PUT",
        "/v1/skills/{skill_id}/improvement-policy",
        "putSkillImprovementPolicy",
    ),
    (
        "POST",
        "/v1/skills/{skill_id}/improvement-policy/run-now",
        "runSkillImprovementPolicyNow",
    ),
    (
        "POST",
        "/v1/skills/{skill_id}/evaluation-suites",
        "createSkillEvaluationSuite",
    ),
    (
        "GET",
        "/v1/skills/{skill_id}/evaluation-suites",
        "listSkillEvaluationSuites",
    ),
    (
        "GET",
        "/v1/skills/evaluation-suites/{suite_id}",
        "getSkillEvaluationSuite",
    ),
    (
        "POST",
        "/v1/skills/evaluation-suites/{suite_id}/runs",
        "runSkillEvaluation",
    ),
    (
        "GET",
        "/v1/skills/evaluation-runs/{run_id}",
        "getSkillEvaluationRun",
    ),
    ("GET", "/v1/tasks/{task_id}", "getTask"),
    ("POST", "/v1/tasks/{task_id}/runs", "startRun"),
    ("GET", "/v1/tasks/{task_id}/schedules", "listSchedules"),
    ("POST", "/v1/tasks/{task_id}/schedules", "createSchedule"),
    ("GET", "/v1/tasks/{task_id}/schedules/{schedule_id}", "getSchedule"),
    ("POST", "/v1/tasks/{task_id}/schedules/{schedule_id}/pause", "pauseSchedule"),
    ("POST", "/v1/tasks/{task_id}/schedules/{schedule_id}/resume", "resumeSchedule"),
    ("POST", "/v1/tasks/{task_id}/schedules/{schedule_id}/cancel", "cancelSchedule"),
    ("GET", "/v1/tasks/{task_id}/schedules/{schedule_id}/fires", "listScheduleFires"),
    (
        "GET",
        "/v1/tasks/{task_id}/schedules/{schedule_id}/delivery-policy",
        "getScheduleDeliveryPolicy",
    ),
    (
        "PUT",
        "/v1/tasks/{task_id}/schedules/{schedule_id}/delivery-policy",
        "putScheduleDeliveryPolicy",
    ),
    ("GET", "/v1/tasks/{task_id}/runs", "listRunsForTask"),
    ("POST", "/v1/tasks/{task_id}/cancel", "cancelTask"),
    ("POST", "/v1/tasks/{task_id}/complete", "completeTask"),
    ("POST", "/v1/tasks/{task_id}/fail", "failTask"),
    ("GET", "/v1/tasks/{task_id}/events", "listTaskEvents"),
    ("GET", "/v1/runs/{run_id}", "getRun"),
    ("GET", "/v1/runs/{run_id}/result", "getRunResult"),
    ("GET", "/v1/runs/{run_id}/execution", "listRunsByExecution"),
    ("GET", "/v1/runs/{run_id}/latest-in-execution", "getLatestRunForExecution"),
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
    ("POST", "/v1/agents", "createAgent"),
    ("GET", "/v1/agents", "listAgents"),
    ("GET", "/v1/agents/{agent_id}", "getAgent"),
    ("POST", "/v1/agents/{agent_id}/revisions", "createAgentRevision"),
    ("GET", "/v1/agents/{agent_id}/revisions", "listAgentRevisions"),
    (
        "POST",
        "/v1/agents/{agent_id}/revisions/{revision_id}/activate",
        "activateAgentRevision",
    ),
    ("POST", "/v1/agents/{agent_id}/disable", "disableAgent"),
    ("POST", "/v1/agents/{agent_id}/archive", "archiveAgent"),
    ("POST", "/v1/workflows", "createWorkflow"),
    ("GET", "/v1/workflows", "listWorkflows"),
    ("GET", "/v1/workflows/{workflow_id}", "getWorkflow"),
    ("POST", "/v1/workflows/{workflow_id}/revisions", "createWorkflowRevision"),
    ("GET", "/v1/workflows/{workflow_id}/revisions", "listWorkflowRevisions"),
    ("GET", "/v1/workflows/{workflow_id}/revisions/{revision_id}", "getWorkflowRevision"),
    (
        "POST",
        "/v1/workflows/{workflow_id}/revisions/{revision_id}/activate",
        "activateWorkflowRevision",
    ),
    ("POST", "/v1/workflows/{workflow_id}/disable", "disableWorkflow"),
    ("POST", "/v1/workflows/{workflow_id}/archive", "archiveWorkflow"),
    ("GET", "/v1/tasks/{task_id}/agent", "getTaskAgent"),
    ("PUT", "/v1/tasks/{task_id}/agent", "putTaskAgent"),
    ("GET", "/v1/tasks/{task_id}/workflow", "getTaskWorkflow"),
    ("PUT", "/v1/tasks/{task_id}/workflow", "putTaskWorkflow"),
    ("DELETE", "/v1/tasks/{task_id}/workflow", "deleteTaskWorkflow"),
    ("GET", "/v1/runs/{run_id}/agent", "getRunAgent"),
    ("POST", "/v1/runs/{run_id}/delegations", "createDelegationRequest"),
    ("GET", "/v1/runs/{run_id}/delegations", "listDelegationRequestsForRun"),
    ("GET", "/v1/delegations/{delegation_id}", "getDelegationRequest"),
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
