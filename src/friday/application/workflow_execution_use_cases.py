"""Application use cases for frozen Workflow execution and DAG scheduling.

The use cases only orchestrate ordinary Friday Tasks and Runs.  They never
invoke a brain or a tool and never copy authority from a parent Run to a
Workflow node.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import cast

from friday.application.brain_runtime_registry import BrainRuntimeRegistry
from friday.application.errors import (
    ClaimLost,
    RunNotFound,
    TaskNotFound,
    WorkflowBindingError,
    WorkflowExecutionError,
    WorkflowIntegrityError,
    WorkflowNotFound,
    WorkflowRevisionNotFound,
)
from friday.application.lifecycle_events import LifecycleEvents
from friday.application.ports import (
    Clock,
    UnitOfWork,
    UnitOfWorkFactory,
    WorkflowExecutionUnitOfWork,
)
from friday.application.start_run import StartRun
from friday.application.workflow_context import (
    WorkflowNodeContextTooLarge,
    build_workflow_node_context,
)
from friday.domain import (
    AgentStatus,
    RunWorkflowResolution,
    RunWorkflowResolutionId,
    Task,
    TaskAgentBinding,
    TaskId,
    TaskWorkflowBinding,
    Workflow,
    WorkflowExecution,
    WorkflowExecutionId,
    WorkflowExecutionStatus,
    WorkflowNode,
    WorkflowNodeExecution,
    WorkflowNodeExecutionId,
    WorkflowNodeExecutionStatus,
    WorkflowRevision,
    WorkflowStatus,
    canonical_workflow_content,
    validate_workflow_dag,
)
from friday.domain.agent import AgentRevision
from friday.domain.errors import DomainValidationError
from friday.domain.event import RunEventType
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import (
    AgentId,
    RunId,
    WorkflowId,
    WorkflowNodeId,
    WorkflowRevisionId,
)
from friday.domain.json_value import JsonValue
from friday.domain.run import Run, RunStatus
from friday.domain.workflow import WorkflowEdge, validate_workflow_revision_ownership


def _workflow_uow(uow: UnitOfWork) -> WorkflowExecutionUnitOfWork:
    return cast(WorkflowExecutionUnitOfWork, uow)


def _workflow_digest(revision: WorkflowRevision) -> str:
    """Recompute the complete immutable graph digest, including all edges."""
    content = canonical_workflow_content(list(revision.nodes), list(revision.edges))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _validate_revision_integrity(workflow: Workflow, revision: WorkflowRevision) -> str:
    if revision.workflow_id != workflow.id:
        raise WorkflowIntegrityError("workflow revision ownership mismatch")
    try:
        validate_workflow_revision_ownership(
            revision.id, list(revision.nodes), list(revision.edges)
        )
        validate_workflow_dag(list(revision.nodes), list(revision.edges))
        digest = _workflow_digest(revision)
    except (DomainValidationError, TypeError, ValueError) as exc:
        raise WorkflowIntegrityError("workflow graph integrity verification failed") from exc
    if digest != revision.content_sha256:
        raise WorkflowIntegrityError("workflow graph digest does not match revision")
    return digest


def _require_active_claim(
    uow: UnitOfWork,
    run_id: RunId,
    worker_id: str,
    claim_token: str,
    claim_generation: int,
    now: datetime,
) -> None:
    if not uow.work_queue.is_claim_active(run_id, worker_id, claim_token, claim_generation, now):
        raise ClaimLost(f"workflow operation requires an exact active claim for run {run_id}")


def _load_active_revision(
    uow: UnitOfWork, workflow_id: WorkflowId
) -> tuple[Workflow, WorkflowRevision, str]:
    workflow = uow.workflows.get(workflow_id)
    if workflow is None:
        raise WorkflowNotFound(workflow_id)
    if workflow.status is WorkflowStatus.ARCHIVED:
        raise WorkflowBindingError("archived workflow cannot be newly resolved")
    if workflow.status is not WorkflowStatus.ACTIVE or workflow.active_revision_id is None:
        raise WorkflowBindingError("workflow must be active with an active revision")
    revision = uow.workflow_revisions.get(workflow.active_revision_id)
    if revision is None:
        raise WorkflowRevisionNotFound(workflow.active_revision_id)
    return workflow, revision, _validate_revision_integrity(workflow, revision)


def _load_frozen_revision(
    uow: UnitOfWork,
    workflow_id: WorkflowId,
    revision_id: WorkflowRevisionId,
    expected_digest: str,
) -> tuple[Workflow, WorkflowRevision]:
    workflow = uow.workflows.get(workflow_id)
    if workflow is None:
        raise WorkflowNotFound(workflow_id)
    revision = uow.workflow_revisions.get(revision_id)
    if revision is None:
        raise WorkflowRevisionNotFound(revision_id)
    digest = _validate_revision_integrity(workflow, revision)
    if digest != expected_digest or revision.content_sha256 != expected_digest:
        raise WorkflowIntegrityError("frozen workflow resolution digest mismatch")
    return workflow, revision


def _ensure_agent_snapshot(
    uow: UnitOfWork,
    node: WorkflowNode,
    runtime_registry: BrainRuntimeRegistry,
) -> AgentRevision:
    agent = uow.agents.get(node.target_agent_id)
    if agent is None:
        raise WorkflowIntegrityError("workflow target Agent is missing")
    if agent.status is not AgentStatus.ACTIVE or agent.active_revision_id is None:
        raise WorkflowIntegrityError(
            f"workflow target agent {node.target_agent_id} is not active and resolvable"
        )
    try:
        revision = uow.agent_revisions.get(agent.active_revision_id)
    except DomainValidationError as exc:
        raise WorkflowIntegrityError("workflow target Agent revision integrity failed") from exc
    if revision is None:
        raise WorkflowIntegrityError("workflow target Agent revision is missing")
    if revision.agent_id != agent.id:
        raise WorkflowIntegrityError("workflow target Agent revision ownership mismatch")
    try:
        expected_revision_digest = hashlib.sha256(
            json.dumps(
                {
                    "instructions": revision.instructions,
                    "runtime_kind": revision.runtime_kind,
                    "runtime_config": revision.runtime_config,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    except (TypeError, ValueError) as exc:
        raise WorkflowIntegrityError("workflow target Agent revision is not valid JSON") from exc
    if revision.content_sha256 != expected_revision_digest:
        raise WorkflowIntegrityError("workflow target Agent revision digest mismatch")
    try:
        if not runtime_registry.is_registered(revision.runtime_kind):
            raise WorkflowIntegrityError("workflow target Agent runtime is not registered")
        runtime_registry.validate_runtime_config(revision.runtime_kind, revision.runtime_config)
    except WorkflowIntegrityError:
        raise
    except Exception as exc:
        raise WorkflowIntegrityError(
            "workflow target Agent runtime configuration is invalid"
        ) from exc
    return revision


def _persist_node(uow: UnitOfWork, node: WorkflowNodeExecution) -> None:
    _workflow_uow(uow).workflow_node_executions.update_status(
        node.id,
        node.status,
        child_task_id=node.child_task_id,
        child_run_id=node.child_run_id,
        child_execution_id=node.child_execution_id,
        result_payload=node.result_payload,
        failure_code=node.failure_code,
        failure_message=node.failure_message,
        started_at=node.started_at,
        completed_at=node.completed_at,
    )


def _persist_execution(uow: UnitOfWork, execution: WorkflowExecution) -> None:
    _workflow_uow(uow).workflow_executions.update_status(
        execution.id,
        execution.status,
        completed_at=execution.completed_at,
        failure_code=execution.failure_code,
        failure_message=execution.failure_message,
    )


def _emit_workflow_event(
    uow: UnitOfWork,
    execution: WorkflowExecution,
    event_type: RunEventType,
    payload: dict[str, object],
    now: datetime,
) -> None:
    root_run = uow.runs.get(execution.root_run_id)
    if root_run is None:
        return
    marker = payload.get("node_execution_id", payload.get("workflow_execution_id"))
    if marker is not None and any(
        event.type is event_type
        and isinstance(event.payload, dict)
        and event.payload.get("node_execution_id", event.payload.get("workflow_execution_id"))
        == marker
        for event in uow.events.list_for_run(root_run.id)
    ):
        return
    LifecycleEvents.append_run_events(
        uow,
        root_run,
        now,
        [(event_type, cast(JsonValue, payload), None)],
    )


def _emit_node_event(
    uow: UnitOfWork,
    node: WorkflowNodeExecution,
    event_type: RunEventType,
    now: datetime,
) -> None:
    execution = _workflow_uow(uow).workflow_executions.get(node.workflow_execution_id)
    if execution is not None:
        payload = {
            "workflow_execution_id": str(execution.id),
            "node_execution_id": str(node.id),
            "node_key": node.node_key,
        }
        _emit_workflow_event(uow, execution, event_type, cast(dict[str, object], payload), now)


def _root_workflow_result(
    execution: WorkflowExecution,
    workflow: Workflow,
    revision: WorkflowRevision,
    nodes: Iterable[WorkflowNodeExecution],
) -> dict[str, object]:
    ordered = sorted(
        nodes,
        key=lambda value: (value.node_key, str(value.id)),
    )
    return {
        "workflow_execution_id": str(execution.id),
        "workflow_id": str(workflow.id),
        "workflow_key": workflow.key,
        "workflow_revision_id": str(revision.id),
        "workflow_revision_version": revision.version,
        "workflow_revision_sha256": execution.workflow_content_sha256,
        "nodes": {node.node_key: node.result_payload for node in ordered},
    }


def _dispatch_node(
    uow: UnitOfWork,
    node_execution: WorkflowNodeExecution,
    node: WorkflowNode,
    now: datetime,
) -> None:
    if node_execution.status is not WorkflowNodeExecutionStatus.PENDING:
        return
    # Fail closed before any child Task/Run creation: the dependent node's
    # complete deterministic predecessor context must fit the durable bound.
    # An oversized result or aggregate must never be silently truncated (that
    # would hand the brain corrupted JSON), so the node is marked BLOCKED and
    # the Workflow eventually fails deterministically.
    try:
        build_workflow_node_context(uow, node_execution)
    except WorkflowNodeContextTooLarge as exc:
        node_execution.block(now, "workflow_context_too_large", str(exc))
        _persist_node(uow, node_execution)
        _emit_node_event(
            uow,
            node_execution,
            RunEventType.WORKFLOW_NODE_BLOCKED,
            now,
        )
        return
    child_task = Task.new(
        id=TaskId.new(),
        title=f"Workflow: {node.node_key}"[:4_000],
        description=node.objective,
        created_at=now,
    )
    uow.tasks.add(child_task)
    # A Workflow node gets the same ordinary TaskAgentBinding as any other
    # child Run.  The binding is provenance, not authority, and the exact
    # Agent revision is checked later by the child Run resolver.
    uow.task_agent_bindings.replace(
        child_task.id,
        TaskAgentBinding(child_task.id, node_execution.target_agent_id, now),
    )
    child_result = StartRun.execute_in_uow(uow, child_task, now)
    if child_result.run_id is None:
        raise WorkflowExecutionError("workflow child Run was not materialized")
    child_run = uow.runs.get(child_result.run_id)
    if child_run is None:
        raise WorkflowExecutionError("workflow child Run disappeared during dispatch")
    node_execution.dispatch(
        child_task.id,
        child_run.id,
        child_run.execution_id,
        now,
    )
    _persist_node(uow, node_execution)
    _emit_node_event(
        uow,
        node_execution,
        RunEventType.WORKFLOW_NODE_DISPATCHED,
        now,
    )


def _require_binding_exclusive(uow: UnitOfWork, task_id: TaskId) -> None:
    if uow.task_agent_bindings.get(task_id) is not None:
        raise WorkflowBindingError(
            "a Task cannot have both TaskAgentBinding and TaskWorkflowBinding"
        )


def _remove_root_claim(
    uow: UnitOfWork,
    run_id: RunId,
    worker_id: str,
    claim_token: str,
    claim_generation: int,
    now: datetime,
) -> None:
    """Remove the root Run work item only if this exact claim still holds it."""
    if not uow.work_queue.remove_if_claimed(run_id, worker_id, claim_token, claim_generation, now):
        raise ClaimLost("Workflow bootstrap lost the exact root Run claim")


def _schedule_to_fixed_point(
    uow: UnitOfWork,
    node_executions: dict[WorkflowNodeId, WorkflowNodeExecution],
    workflow_nodes: dict[WorkflowNodeId, WorkflowNode],
    predecessors: dict[WorkflowNodeId, list[WorkflowNodeId]],
    now: datetime,
) -> None:
    """Advance every PENDING node to a durable fixed point.

    A node whose predecessor failed, cancelled, or blocked is itself blocked;
    a node whose predecessors all succeeded is dispatched.  The loop repeats
    until no node changes, so a BLOCKED root propagates through the whole
    downstream DAG in one call.
    """
    while True:
        changed = False
        for node_id in sorted(
            node_executions,
            key=lambda value: (workflow_nodes[value].node_key, str(value)),
        ):
            node_execution = node_executions[node_id]
            if node_execution.status is not WorkflowNodeExecutionStatus.PENDING:
                continue
            predecessor_statuses = [
                node_executions[pred].status for pred in predecessors.get(node_id, [])
            ]
            if any(
                status
                in {
                    WorkflowNodeExecutionStatus.FAILED,
                    WorkflowNodeExecutionStatus.CANCELLED,
                    WorkflowNodeExecutionStatus.BLOCKED,
                }
                for status in predecessor_statuses
            ):
                node_execution.block(now)
                _persist_node(uow, node_execution)
                _emit_node_event(
                    uow,
                    node_execution,
                    RunEventType.WORKFLOW_NODE_BLOCKED,
                    now,
                )
                changed = True
            elif all(
                status is WorkflowNodeExecutionStatus.SUCCEEDED for status in predecessor_statuses
            ):
                _dispatch_node(uow, node_execution, workflow_nodes[node_id], now)
                changed = True
        if not changed:
            return


def _finish_if_terminal(
    uow: UnitOfWork,
    execution: WorkflowExecution,
    nodes: list[WorkflowNodeExecution],
    now: datetime,
    queue_remover: Callable[[RunId], None] | None = None,
) -> None:
    """Terminalize the Workflow execution and root Run when every node is done.

    ``queue_remover`` lets bootstrap remove the root work item under its exact
    claim (``remove_if_claimed``) while reconcile uses the plain ``remove``
    (the item was already removed during bootstrap).
    """
    if not nodes or any(not node.is_terminal for node in nodes):
        return
    root_run = uow.runs.get(execution.root_run_id)
    if (
        root_run is not None
        and root_run.status is RunStatus.WAITING_FOR_WORKFLOW
        and root_run.workflow_execution_id != execution.id
    ):
        raise WorkflowIntegrityError("root Run Workflow ownership marker mismatch")
    remover = queue_remover or uow.work_queue.remove
    if all(node.status is WorkflowNodeExecutionStatus.SUCCEEDED for node in nodes):
        workflow, revision = _load_frozen_revision(
            uow,
            execution.workflow_id,
            execution.workflow_revision_id,
            execution.workflow_content_sha256,
        )
        result = _root_workflow_result(execution, workflow, revision, nodes)
        execution.succeed(now)
        _persist_execution(uow, execution)
        _emit_workflow_event(
            uow,
            execution,
            RunEventType.WORKFLOW_EXECUTION_SUCCEEDED,
            result,
            now,
        )
        if root_run is not None and root_run.status is RunStatus.WAITING_FOR_WORKFLOW:
            root_run.succeed_workflow(now)
            uow.runs.save(root_run)
            remover(root_run.id)
        return

    failed = next(
        (node for node in nodes if node.status is WorkflowNodeExecutionStatus.FAILED),
        None,
    )
    cancelled = next(
        (node for node in nodes if node.status is WorkflowNodeExecutionStatus.CANCELLED),
        None,
    )
    if failed is not None:
        code = failed.failure_code or "workflow_node_failed"
        message = failed.failure_message or "A Workflow node failed"
        execution.fail(now, code, message)
        _persist_execution(uow, execution)
        _emit_workflow_event(
            uow,
            execution,
            RunEventType.WORKFLOW_EXECUTION_FAILED,
            {
                "workflow_execution_id": str(execution.id),
                "failure_code": code,
                "failure_message": message,
            },
            now,
        )
        if root_run is not None and root_run.status is RunStatus.WAITING_FOR_WORKFLOW:
            root_run.fail_workflow(now, Failure(code, message, False, FailureCause.RUNTIME))
            uow.runs.save(root_run)
            remover(root_run.id)
    elif cancelled is not None:
        execution.cancel(now, "A Workflow node was cancelled")
        _persist_execution(uow, execution)
        _emit_workflow_event(
            uow,
            execution,
            RunEventType.WORKFLOW_EXECUTION_FAILED,
            {
                "workflow_execution_id": str(execution.id),
                "failure_code": "workflow_node_cancelled",
                "failure_message": "A Workflow node was cancelled",
            },
            now,
        )
        if root_run is not None and root_run.status is RunStatus.WAITING_FOR_WORKFLOW:
            root_run.cancel_workflow(now)
            uow.runs.save(root_run)
            remover(root_run.id)
    else:
        blocked = next(
            (node for node in nodes if node.status is WorkflowNodeExecutionStatus.BLOCKED),
            None,
        )
        if blocked is not None and blocked.failure_code != "blocked_by_predecessor":
            code = blocked.failure_code or "workflow_node_blocked"
            message = blocked.failure_message or "A Workflow node was blocked by a predecessor"
        else:
            code = "workflow_node_blocked"
            message = "A Workflow node was blocked by a predecessor"
        execution.fail(now, code, message)
        _persist_execution(uow, execution)
        _emit_workflow_event(
            uow,
            execution,
            RunEventType.WORKFLOW_EXECUTION_FAILED,
            {
                "workflow_execution_id": str(execution.id),
                "failure_code": code,
                "failure_message": message,
            },
            now,
        )
        if root_run is not None and root_run.status is RunStatus.WAITING_FOR_WORKFLOW:
            root_run.fail_workflow(now, Failure(code, message, False, FailureCause.RUNTIME))
            uow.runs.save(root_run)
            remover(root_run.id)


class BindTaskWorkflow:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(self, *, task_id: TaskId, workflow_id: WorkflowId) -> TaskWorkflowBinding:
        with self._uow_factory() as uow:
            workflow_uow = _workflow_uow(uow)
            if uow.tasks.get(task_id) is None:
                raise TaskNotFound(task_id)
            workflow = uow.workflows.get(workflow_id)
            if workflow is None:
                raise WorkflowNotFound(workflow_id)
            if workflow.status is WorkflowStatus.ARCHIVED:
                raise WorkflowBindingError("archived workflow cannot be newly bound")
            _require_binding_exclusive(uow, task_id)
            existing = workflow_uow.task_workflow_bindings.get_by_task_id(task_id)
            now = self._clock.now()
            binding = (
                existing.updated(workflow_id=workflow_id, at=now)
                if existing is not None
                else TaskWorkflowBinding.new(task_id=task_id, workflow_id=workflow_id, at=now)
            )
            workflow_uow.task_workflow_bindings.bind(binding)
            uow.commit()
            return binding


class UnbindTaskWorkflow:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def execute(self, *, task_id: TaskId) -> None:
        with self._uow_factory() as uow:
            if uow.tasks.get(task_id) is None:
                raise TaskNotFound(task_id)
            _workflow_uow(uow).task_workflow_bindings.unbind(task_id)
            uow.commit()


class ResolveRunWorkflow:
    """Freeze one queued/root Run to one exact Workflow revision under claim."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(
        self,
        run_id: RunId,
        workflow_id: WorkflowId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
    ) -> RunWorkflowResolution:
        with self._uow_factory() as uow:
            workflow_uow = _workflow_uow(uow)
            now = self._clock.now()
            _require_active_claim(uow, run_id, worker_id, claim_token, claim_generation, now)
            run = uow.runs.get(run_id)
            if run is None:
                raise RunNotFound(run_id)
            _require_binding_exclusive(uow, run.task_id)
            existing = workflow_uow.run_workflow_resolutions.get_by_run_id(run.id)
            if existing is not None:
                if existing.workflow_id != workflow_id:
                    raise WorkflowBindingError("Run is already frozen to another Workflow")
                _load_frozen_revision(
                    uow,
                    existing.workflow_id,
                    existing.workflow_revision_id,
                    existing.content_sha256,
                )
                if not uow.work_queue.is_claim_active(
                    run.id, worker_id, claim_token, claim_generation, self._clock.now()
                ):
                    raise ClaimLost("workflow resolution claim expired")
                return existing

            binding = workflow_uow.task_workflow_bindings.get_by_task_id(run.task_id)
            if binding is None or binding.workflow_id != workflow_id:
                raise WorkflowBindingError("Run does not have the requested Workflow binding")
            if run.status not in {RunStatus.QUEUED, RunStatus.RUNNING}:
                raise WorkflowBindingError("only an unresolved queued or running Run can resolve")
            workflow, revision, digest = _load_active_revision(uow, workflow_id)
            resolution = RunWorkflowResolution(
                RunWorkflowResolutionId.new(),
                run.id,
                workflow.id,
                revision.id,
                digest,
                now,
            )
            if not workflow_uow.run_workflow_resolutions.add_if_claimed(
                resolution,
                worker_id,
                claim_token,
                claim_generation,
                now,
            ):
                raise ClaimLost("workflow resolution claim was lost before publication")
            if not uow.work_queue.is_claim_active(
                run.id, worker_id, claim_token, claim_generation, self._clock.now()
            ):
                raise ClaimLost("workflow resolution claim expired before commit")
            uow.commit()
            return resolution


class StartWorkflowExecution:
    """Atomically bootstrap one frozen Workflow DAG under the root Run claim."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        runtime_registry: BrainRuntimeRegistry,
    ) -> None:
        self._uow_factory, self._clock = uow_factory, clock
        self._runtime_registry = runtime_registry

    def execute(
        self,
        root_run_id: RunId,
        workflow_id: WorkflowId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
    ) -> WorkflowExecution:
        with self._uow_factory() as uow:
            workflow_uow = _workflow_uow(uow)
            root_run = uow.runs.get(root_run_id)
            if root_run is None:
                raise RunNotFound(root_run_id)
            existing = workflow_uow.workflow_executions.list_by_root_run_id(root_run_id)
            if len(existing) > 1:
                raise WorkflowIntegrityError("multiple Workflow executions exist for one root Run")
            if existing:
                execution = existing[0]
                if execution.workflow_id != workflow_id:
                    raise WorkflowBindingError("root Run is already owned by another Workflow")
                if execution.root_run_id != root_run_id:
                    raise WorkflowIntegrityError(
                        "workflow execution root Run does not match the lookup key"
                    )
                if root_run.workflow_execution_id != execution.id:
                    raise WorkflowIntegrityError(
                        "root Run does not point to its Workflow execution"
                    )
                if root_run.status not in {
                    RunStatus.WAITING_FOR_WORKFLOW,
                    RunStatus.SUCCEEDED,
                    RunStatus.FAILED,
                    RunStatus.CANCELLED,
                }:
                    raise WorkflowIntegrityError(
                        "existing Workflow execution has an invalid root Run state"
                    )
                resolution = workflow_uow.run_workflow_resolutions.get_by_run_id(root_run.id)
                if (
                    resolution is None
                    or resolution.run_id != root_run.id
                    or resolution.workflow_id != execution.workflow_id
                    or resolution.workflow_revision_id != execution.workflow_revision_id
                    or resolution.content_sha256 != execution.workflow_content_sha256
                ):
                    raise WorkflowIntegrityError(
                        "existing Workflow execution does not match its frozen resolution"
                    )
                _load_frozen_revision(
                    uow,
                    execution.workflow_id,
                    execution.workflow_revision_id,
                    execution.workflow_content_sha256,
                )
                uow.commit()
                return execution

            now = self._clock.now()
            _require_active_claim(uow, root_run_id, worker_id, claim_token, claim_generation, now)
            if root_run.status is not RunStatus.RUNNING:
                raise WorkflowExecutionError("Workflow bootstrap requires a RUNNING root Run")
            if root_run.workflow_execution_id is not None:
                raise WorkflowIntegrityError("root Run references a missing Workflow execution")
            _require_binding_exclusive(uow, root_run.task_id)

            resolution = workflow_uow.run_workflow_resolutions.get_by_run_id(root_run.id)
            if resolution is None:
                binding = workflow_uow.task_workflow_bindings.get_by_task_id(root_run.task_id)
                if binding is None or binding.workflow_id != workflow_id:
                    raise WorkflowBindingError(
                        "root Run does not have the requested Workflow binding"
                    )
                workflow, revision, digest = _load_active_revision(uow, workflow_id)
                resolution = RunWorkflowResolution(
                    RunWorkflowResolutionId.new(),
                    root_run.id,
                    workflow.id,
                    revision.id,
                    digest,
                    now,
                )
            elif resolution.workflow_id != workflow_id:
                raise WorkflowBindingError("root Run is already frozen to another Workflow")
            if resolution.run_id != root_run.id:
                raise WorkflowIntegrityError("Run Workflow resolution ownership mismatch")

            workflow, revision = _load_frozen_revision(
                uow,
                resolution.workflow_id,
                resolution.workflow_revision_id,
                resolution.content_sha256,
            )

            # Perform every potentially failing Agent/runtime validation before
            # creating any execution, node, Task, Run, or queue state.
            agent_revisions: dict[AgentId, AgentRevision] = {}
            for node in sorted(revision.nodes, key=lambda value: (value.node_key, str(value.id))):
                agent_revisions[node.target_agent_id] = _ensure_agent_snapshot(
                    uow, node, self._runtime_registry
                )

            if workflow_uow.run_workflow_resolutions.get_by_run_id(root_run.id) is None:
                workflow_uow.run_workflow_resolutions.create(resolution)

            execution = WorkflowExecution(
                id=WorkflowExecutionId.new(),
                root_run_id=root_run.id,
                workflow_id=workflow.id,
                workflow_revision_id=revision.id,
                workflow_content_sha256=resolution.content_sha256,
                status=WorkflowExecutionStatus.RUNNING,
                started_at=now,
            )
            workflow_uow.workflow_executions.create(execution)
            _emit_workflow_event(
                uow,
                execution,
                RunEventType.WORKFLOW_EXECUTION_STARTED,
                {"workflow_execution_id": str(execution.id)},
                now,
            )

            node_executions: dict[WorkflowNodeId, WorkflowNodeExecution] = {}
            for node in sorted(revision.nodes, key=lambda value: (value.node_key, str(value.id))):
                agent_revision = agent_revisions[node.target_agent_id]
                node_execution = WorkflowNodeExecution(
                    id=WorkflowNodeExecutionId.new(),
                    workflow_execution_id=execution.id,
                    workflow_node_id=node.id,
                    workflow_revision_id=revision.id,
                    node_key=node.node_key,
                    target_agent_id=node.target_agent_id,
                    target_agent_revision_id=agent_revision.id,
                    target_agent_revision_sha256=agent_revision.content_sha256,
                    status=WorkflowNodeExecutionStatus.PENDING,
                    created_at=now,
                )
                workflow_uow.workflow_node_executions.create(node_execution)
                node_executions[node.id] = node_execution

            predecessors = _predecessors(revision.edges)
            by_id = {node.id: node for node in revision.nodes}
            for node_id in sorted(
                node_executions,
                key=lambda value: (by_id[value].node_key, str(value)),
            ):
                if not predecessors.get(node_id, []):
                    _dispatch_node(uow, node_executions[node_id], by_id[node_id], now)

            # Reach a durable fixed point in the same transaction: a root that
            # becomes BLOCKED (e.g. workflow_context_too_large) must propagate
            # through the whole downstream DAG and terminalize the Workflow
            # immediately, otherwise a single oversized root would strand the
            # Workflow in WAITING_FOR_WORKFLOW forever with no child to trigger
            # a later reconcile.
            _schedule_to_fixed_point(uow, node_executions, by_id, predecessors, now)
            root_run.wait_for_workflow(now, execution.id)
            uow.runs.save(root_run)
            _finish_if_terminal(
                uow,
                execution,
                list(node_executions.values()),
                now,
                queue_remover=lambda run_id: _remove_root_claim(
                    uow, run_id, worker_id, claim_token, claim_generation, now
                ),
            )
            if execution.status is WorkflowExecutionStatus.RUNNING:
                _remove_root_claim(uow, root_run.id, worker_id, claim_token, claim_generation, now)
            uow.commit()
            return execution


def _predecessors(edges: Iterable[WorkflowEdge]) -> dict[WorkflowNodeId, list[WorkflowNodeId]]:
    result: dict[WorkflowNodeId, list[WorkflowNodeId]] = {}
    for edge in edges:
        from_node_id = edge.from_node_id
        to_node_id = edge.to_node_id
        result.setdefault(from_node_id, [])
        result.setdefault(to_node_id, []).append(from_node_id)
    return result


def _failure_from_child(run: Run) -> tuple[str, str]:
    if run.failure is not None:
        return run.failure.code, run.failure.message
    return "child_run_failed", "Workflow child Run failed"


def _bounded_child_result(
    uow: UnitOfWork, child_run: Run, completed_at: datetime
) -> dict[str, object]:
    event = uow.events.latest_of_type_for_run(child_run.id, RunEventType.AGENT_FINISHED)
    summary = ""
    details: object = None
    if event is not None and isinstance(event.payload, dict):
        raw_summary = event.payload.get("summary")
        summary = raw_summary if isinstance(raw_summary, str) else ""
        details = event.payload.get("details")
    result: dict[str, object] = {
        "child_run_id": str(child_run.id),
        "child_execution_id": str(child_run.execution_id),
        "completed_at": (child_run.ended_at or completed_at).isoformat(),
        "summary": summary[:4000],
    }
    if details is not None:
        result["details"] = details
    if len(json.dumps(result, sort_keys=True, separators=(",", ":"))) > 8000:
        result.pop("details", None)
    return result


class ReconcileWorkflowExecution:
    """Advance a Workflow DAG to a durable fixed point after child outcomes."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(self, workflow_execution_id: WorkflowExecutionId) -> list[WorkflowNodeExecution]:
        with self._uow_factory() as uow:
            workflow_uow = _workflow_uow(uow)
            execution = workflow_uow.workflow_executions.get(workflow_execution_id)
            if execution is None:
                raise WorkflowExecutionError("Workflow execution not found")
            nodes = workflow_uow.workflow_node_executions.list_by_execution(execution.id)
            resolution = workflow_uow.run_workflow_resolutions.get_by_run_id(execution.root_run_id)
            if (
                resolution is None
                or resolution.run_id != execution.root_run_id
                or resolution.workflow_id != execution.workflow_id
                or resolution.workflow_revision_id != execution.workflow_revision_id
                or resolution.content_sha256 != execution.workflow_content_sha256
            ):
                raise WorkflowIntegrityError(
                    "Workflow execution does not match its frozen Run resolution"
                )
            workflow, revision = _load_frozen_revision(
                uow,
                execution.workflow_id,
                execution.workflow_revision_id,
                execution.workflow_content_sha256,
            )
            del workflow
            by_node_id = {node.id: node for node in revision.nodes}
            if len(nodes) != len(by_node_id) or {node.workflow_node_id for node in nodes} != set(
                by_node_id
            ):
                raise WorkflowIntegrityError("Workflow node execution set does not match revision")
            if any(node.workflow_execution_id != execution.id for node in nodes):
                raise WorkflowIntegrityError("Workflow node execution ownership mismatch")
            for node_execution in nodes:
                if node_execution.workflow_revision_id != execution.workflow_revision_id:
                    raise WorkflowIntegrityError(
                        "Workflow node execution frozen revision ownership mismatch"
                    )
                definition = by_node_id[node_execution.workflow_node_id]
                if (
                    node_execution.node_key != definition.node_key
                    or node_execution.target_agent_id != definition.target_agent_id
                ):
                    raise WorkflowIntegrityError(
                        "Workflow node execution does not match its frozen node definition"
                    )
                try:
                    agent_revision = uow.agent_revisions.get(
                        node_execution.target_agent_revision_id
                    )
                except DomainValidationError as exc:
                    raise WorkflowIntegrityError(
                        "Workflow node Agent revision snapshot is invalid"
                    ) from exc
                if (
                    agent_revision is None
                    or agent_revision.agent_id != node_execution.target_agent_id
                    or agent_revision.content_sha256 != node_execution.target_agent_revision_sha256
                ):
                    raise WorkflowIntegrityError("Workflow node Agent revision snapshot is invalid")
                if node_execution.child_task_id is not None:
                    child_binding = uow.task_agent_bindings.get(node_execution.child_task_id)
                    if (
                        child_binding is None
                        or child_binding.agent_id != node_execution.target_agent_id
                    ):
                        raise WorkflowIntegrityError("Workflow node child Task binding mismatch")
                if (
                    node_execution.child_task_id is not None
                    and node_execution.child_run_id is not None
                    and node_execution.child_execution_id is not None
                ):
                    child_run = uow.runs.get(node_execution.child_run_id)
                    if (
                        child_run is None
                        or child_run.execution_id != node_execution.child_execution_id
                        or child_run.task_id != node_execution.child_task_id
                    ):
                        raise WorkflowIntegrityError("Workflow node child Run lineage mismatch")
            node_executions = {node.workflow_node_id: node for node in nodes}
            now = self._clock.now()

            if execution.status is WorkflowExecutionStatus.RUNNING:
                self._reconcile_dispatched_children(uow, node_executions.values(), now)
                predecessors = _predecessors(revision.edges)
                _schedule_to_fixed_point(uow, node_executions, by_node_id, predecessors, now)
                _finish_if_terminal(uow, execution, list(node_executions.values()), now)
            uow.commit()
            return sorted(node_executions.values(), key=lambda node: (node.node_key, str(node.id)))

    @staticmethod
    def _reconcile_dispatched_children(
        uow: UnitOfWork,
        node_executions: Iterable[WorkflowNodeExecution],
        now: datetime,
    ) -> None:
        for node in sorted(node_executions, key=lambda value: (value.node_key, str(value.id))):
            if node.status is not WorkflowNodeExecutionStatus.DISPATCHED:
                continue
            if node.child_execution_id is None:
                raise WorkflowIntegrityError("dispatched Workflow node has no child execution")
            child_run = uow.runs.get_latest_for_execution(node.child_execution_id)
            if child_run is None:
                continue
            if child_run.status is RunStatus.SUCCEEDED:
                node.succeed(now, cast(JsonValue, _bounded_child_result(uow, child_run, now)))
                _persist_node(uow, node)
                _emit_node_event(
                    uow,
                    node,
                    RunEventType.WORKFLOW_NODE_SUCCEEDED,
                    now,
                )
            elif child_run.status is RunStatus.FAILED:
                code, message = _failure_from_child(child_run)
                node.fail(now, code, message)
                _persist_node(uow, node)
                _emit_node_event(
                    uow,
                    node,
                    RunEventType.WORKFLOW_NODE_FAILED,
                    now,
                )
            elif child_run.status is RunStatus.CANCELLED:
                node.cancel(now)
                _persist_node(uow, node)
                _emit_node_event(
                    uow,
                    node,
                    RunEventType.WORKFLOW_NODE_CANCELLED,
                    now,
                )
