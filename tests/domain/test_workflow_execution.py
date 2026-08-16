from datetime import UTC, datetime

import pytest

from friday.domain import (
    AgentId,
    AgentRevisionId,
    DomainValidationError,
    InvalidStateTransition,
    Run,
    RunId,
    RunStatus,
    RunWorkflowResolution,
    RunWorkflowResolutionId,
    TaskId,
    TaskWorkflowBinding,
    WorkflowExecution,
    WorkflowExecutionId,
    WorkflowExecutionStatus,
    WorkflowId,
    WorkflowNodeExecution,
    WorkflowNodeExecutionId,
    WorkflowNodeExecutionStatus,
    WorkflowNodeId,
    WorkflowRevisionId,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 1, 1, tzinfo=UTC)
SHA = "a" * 64


def _execution() -> WorkflowExecution:
    return WorkflowExecution(
        id=WorkflowExecutionId.new(),
        root_run_id=RunId.new(),
        workflow_id=WorkflowId.new(),
        workflow_revision_id=WorkflowRevisionId.new(),
        workflow_content_sha256=SHA,
        status=WorkflowExecutionStatus.RUNNING,
        started_at=T0,
    )


def _node(execution: WorkflowExecution | None = None) -> WorkflowNodeExecution:
    execution = execution or _execution()
    return WorkflowNodeExecution(
        id=WorkflowNodeExecutionId.new(),
        workflow_execution_id=execution.id,
        workflow_node_id=WorkflowNodeId.new(),
        workflow_revision_id=execution.workflow_revision_id,
        node_key="node-a",
        target_agent_id=AgentId.new(),
        target_agent_revision_id=AgentRevisionId.new(),
        target_agent_revision_sha256=SHA,
        status=WorkflowNodeExecutionStatus.PENDING,
        created_at=T0,
    )


def test_workflow_execution_status_transitions_are_terminal() -> None:
    execution = _execution()
    execution.succeed(T1)
    assert execution.status is WorkflowExecutionStatus.SUCCEEDED
    assert execution.completed_at == T1

    with pytest.raises(InvalidStateTransition):
        execution.fail(T1, "late", "too late")


def test_failed_execution_requires_failure_shape_and_timestamp_order() -> None:
    with pytest.raises(DomainValidationError):
        WorkflowExecution(
            id=WorkflowExecutionId.new(),
            root_run_id=RunId.new(),
            workflow_id=WorkflowId.new(),
            workflow_revision_id=WorkflowRevisionId.new(),
            workflow_content_sha256="not-a-sha",
            status=WorkflowExecutionStatus.RUNNING,
            started_at=T0,
        )

    execution = _execution()
    with pytest.raises(DomainValidationError):
        execution.fail(T1, "", "missing code")


def test_node_execution_dispatch_and_success_transition() -> None:
    node = _node()
    node.dispatch(TaskId.new(), RunId.new(), RunId.new(), T0)
    assert node.status is WorkflowNodeExecutionStatus.DISPATCHED
    assert node.child_task_id is not None
    node.succeed(T1, {"answer": "ok"})
    assert node.status.value == WorkflowNodeExecutionStatus.SUCCEEDED.value
    assert node.result_payload == {"answer": "ok"}
    assert node.completed_at == T1

    with pytest.raises(InvalidStateTransition):
        node.dispatch(TaskId.new(), RunId.new(), RunId.new(), T1)


def test_node_failure_and_blocked_statuses_are_terminal() -> None:
    failed = _node()
    failed.dispatch(TaskId.new(), RunId.new(), RunId.new(), T0)
    failed.fail(T1, "child_failed", "child failed")
    assert failed.is_terminal

    blocked = _node()
    blocked.block(T1)
    assert blocked.status is WorkflowNodeExecutionStatus.BLOCKED
    assert blocked.failure_code == "blocked_by_predecessor"


def test_resolution_and_binding_are_immutable_values_with_utc_invariants() -> None:
    resolution = RunWorkflowResolution(
        id=RunWorkflowResolutionId.new(),
        run_id=RunId.new(),
        workflow_id=WorkflowId.new(),
        workflow_revision_id=WorkflowRevisionId.new(),
        content_sha256=SHA,
        resolved_at=T0,
    )
    with pytest.raises(AttributeError):
        resolution.workflow_id = WorkflowId.new()  # type: ignore[misc]

    binding = TaskWorkflowBinding.new(task_id=TaskId.new(), workflow_id=WorkflowId.new(), at=T0)
    updated = binding.updated(workflow_id=WorkflowId.new(), at=T1)
    assert updated.created_at == binding.created_at
    assert updated.updated_at == T1
    with pytest.raises(DomainValidationError):
        TaskWorkflowBinding(binding.task_id, binding.workflow_id, T1, T0)


def test_workflow_execution_construction_rejects_invalid_shapes() -> None:
    def _make(**overrides: object) -> WorkflowExecution:
        base = dict(
            id=WorkflowExecutionId.new(),
            root_run_id=RunId.new(),
            workflow_id=WorkflowId.new(),
            workflow_revision_id=WorkflowRevisionId.new(),
            workflow_content_sha256=SHA,
            status=WorkflowExecutionStatus.RUNNING,
            started_at=T0,
        )
        base.update(overrides)
        return WorkflowExecution(**base)  # type: ignore[arg-type]

    with pytest.raises(DomainValidationError):
        _make(completed_at=T0.replace(year=2025))  # completed_at precedes started_at
    with pytest.raises(DomainValidationError):
        _make(status=WorkflowExecutionStatus.RUNNING, completed_at=T1)
    with pytest.raises(DomainValidationError):
        _make(status=WorkflowExecutionStatus.RUNNING, failure_code="x", failure_message="y")
    with pytest.raises(DomainValidationError):
        _make(status=WorkflowExecutionStatus.SUCCEEDED)  # terminal requires completed_at
    with pytest.raises(DomainValidationError):
        _make(
            status=WorkflowExecutionStatus.SUCCEEDED,
            completed_at=T1,
            failure_code="x",
            failure_message="y",
        )
    with pytest.raises(DomainValidationError):
        _make(status=WorkflowExecutionStatus.CANCELLED, completed_at=T1, failure_code="x")


def test_workflow_execution_methods_reject_out_of_order_timestamps() -> None:
    before = T0.replace(year=2025)
    with pytest.raises(DomainValidationError):
        _execution().succeed(before)
    with pytest.raises(DomainValidationError):
        _execution().fail(before, "code", "message")
    with pytest.raises(DomainValidationError):
        _execution().cancel(before)
    with pytest.raises(DomainValidationError):
        _execution().cancel(T1, message="   ")


def test_node_execution_construction_rejects_invalid_shapes() -> None:
    execution = _execution()

    def _make(**overrides: object) -> WorkflowNodeExecution:
        base = dict(
            id=WorkflowNodeExecutionId.new(),
            workflow_execution_id=execution.id,
            workflow_node_id=WorkflowNodeId.new(),
            workflow_revision_id=execution.workflow_revision_id,
            node_key="node-a",
            target_agent_id=AgentId.new(),
            target_agent_revision_id=AgentRevisionId.new(),
            target_agent_revision_sha256=SHA,
            status=WorkflowNodeExecutionStatus.PENDING,
            created_at=T0,
        )
        base.update(overrides)
        return WorkflowNodeExecution(**base)  # type: ignore[arg-type]

    with pytest.raises(DomainValidationError):
        _make(node_key="")
    with pytest.raises(DomainValidationError):
        _make(failure_code="   ")  # whitespace-only code is rejected before status-shape checks
    with pytest.raises(DomainValidationError):
        _make(failure_message="   ")
    with pytest.raises(DomainValidationError):
        _make(started_at=T0.replace(year=2025))  # started_at precedes created_at
    with pytest.raises(DomainValidationError):
        _make(completed_at=T0.replace(year=2025))  # completed_at precedes creation
    with pytest.raises(DomainValidationError):
        _make(started_at=T0)  # pending cannot have a timestamp
    with pytest.raises(DomainValidationError):
        _make(failure_code="x", failure_message="y")  # pending cannot have a failure
    with pytest.raises(DomainValidationError):
        _make(status=WorkflowNodeExecutionStatus.DISPATCHED)  # missing required child shape
    with pytest.raises(DomainValidationError):
        _make(
            status=WorkflowNodeExecutionStatus.DISPATCHED,
            child_task_id=TaskId.new(),
            child_run_id=RunId.new(),
            child_execution_id=RunId.new(),
            started_at=T0,
            failure_code="x",
            failure_message="y",
        )
    with pytest.raises(DomainValidationError):
        _make(status=WorkflowNodeExecutionStatus.SUCCEEDED)  # missing required child shape
    with pytest.raises(DomainValidationError):
        _make(
            status=WorkflowNodeExecutionStatus.SUCCEEDED,
            child_task_id=TaskId.new(),
            child_run_id=RunId.new(),
            child_execution_id=RunId.new(),
            started_at=T0,
            completed_at=T1,
            failure_code="x",
            failure_message="y",
        )
    with pytest.raises(DomainValidationError):
        _make(status=WorkflowNodeExecutionStatus.FAILED)  # missing required child shape and failure
    with pytest.raises(DomainValidationError):
        _make(status=WorkflowNodeExecutionStatus.CANCELLED)  # requires completed_at
    with pytest.raises(DomainValidationError):
        _make(
            status=WorkflowNodeExecutionStatus.CANCELLED,
            completed_at=T1,
            failure_code="x",
            failure_message="y",
        )
    with pytest.raises(DomainValidationError):
        _make(status=WorkflowNodeExecutionStatus.BLOCKED)  # requires completed_at
    with pytest.raises(DomainValidationError):
        _make(
            status=WorkflowNodeExecutionStatus.BLOCKED,
            completed_at=T1,
            child_task_id=TaskId.new(),
        )
    with pytest.raises(DomainValidationError):
        _make(status=WorkflowNodeExecutionStatus.BLOCKED, completed_at=T1)  # requires failure


def test_node_execution_methods_reject_invalid_transitions_and_timestamps() -> None:
    before = T0.replace(year=2025)

    dispatched = _node()
    dispatched.dispatch(TaskId.new(), RunId.new(), RunId.new(), T0)
    with pytest.raises(InvalidStateTransition):
        dispatched.dispatch(TaskId.new(), RunId.new(), RunId.new(), T0)  # not PENDING anymore

    with pytest.raises(DomainValidationError):
        _node().dispatch(TaskId.new(), RunId.new(), RunId.new(), before)

    with pytest.raises(DomainValidationError):
        dispatched.succeed(before)
    with pytest.raises(DomainValidationError):
        dispatched.fail(before, "code", "message")
    with pytest.raises(DomainValidationError):
        dispatched.cancel(before)
    with pytest.raises(DomainValidationError):
        _node().block(before)


def test_root_run_has_separate_workflow_wait_ownership() -> None:
    run = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)
    run.start(T0)
    run.wait_for_workflow(T0, WorkflowExecutionId.new())
    assert run.status is RunStatus.WAITING_FOR_WORKFLOW
    assert run.workflow_execution_id is not None
    run.succeed(T1)
    assert run.status.value == RunStatus.SUCCEEDED.value
