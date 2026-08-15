"""SQLite proofs for the durable Workflow execution/scheduler boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from friday.application.agent_registry import (
    ActivateAgentRevision,
    CreateAgent,
    CreateAgentRevision,
)
from friday.application.brain_runtime_registry import BrainRuntimeRegistry
from friday.application.commands import CancelRunCommand, RetryFailedRunCommand
from friday.application.errors import (
    EntityConflict,
    WorkflowCancelNotSupportedWhileActive,
    WorkflowNodeManualRetryForbidden,
)
from friday.application.ports import UnitOfWorkFactory
from friday.application.run_lifecycle import CancelRun, RetryFailedRun
from friday.application.workflow_execution_use_cases import (
    ReconcileWorkflowExecution,
    StartWorkflowExecution,
)
from friday.application.workflow_registry import (
    ActivateWorkflowRevision,
    CreateWorkflow,
    CreateWorkflowRevision,
)
from friday.domain import (
    Agent,
    AgentRevisionSourceKind,
    Run,
    RunStatus,
    RunWorkflowResolution,
    RunWorkflowResolutionId,
    Task,
    TaskWorkflowBinding,
    Workflow,
    WorkflowExecution,
    WorkflowExecutionId,
    WorkflowExecutionStatus,
    WorkflowNodeExecution,
    WorkflowNodeExecutionId,
    WorkflowNodeExecutionStatus,
    WorkflowRevision,
    WorkflowRevisionSourceKind,
)
from friday.domain.event import RunEventType
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import AgentRevisionId, RunId, TaskId
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory

REPO_ROOT = Path(__file__).resolve().parents[2]
AT = datetime(2026, 8, 12, 12, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return AT


def _factory(tmp_path: Path) -> UnitOfWorkFactory:
    database_url = f"sqlite:///{tmp_path / 'workflow-execution.db'}"
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return create_unit_of_work_factory(create_session_factory(create_engine(database_url)))


def _runtime_registry() -> BrainRuntimeRegistry:
    registry = BrainRuntimeRegistry()
    registry.register("claude_cli", lambda: None)  # type: ignore[arg-type,return-value]
    return registry


def _seed(
    factory: UnitOfWorkFactory,
) -> tuple[Task, Run, Agent, Workflow, WorkflowRevision]:
    clock = _Clock()
    agent = CreateAgent(factory, clock).execute(
        key="persist.workflow.agent",
        display_name="Workflow Agent",
        description="",
    )
    revision = CreateAgentRevision(factory, clock, _runtime_registry()).execute(
        agent_id=agent.id,
        instructions="execute the node",
        runtime_kind="claude_cli",
        runtime_config={},
        source_kind=AgentRevisionSourceKind.OPERATOR,
    )
    ActivateAgentRevision(factory, clock).execute(agent_id=agent.id, revision_id=revision.id)
    workflow = CreateWorkflow(factory, clock).execute(
        key="persist.workflow",
        display_name="Persisted Workflow",
        description="",
    )
    workflow_revision = CreateWorkflowRevision(factory, clock).execute(
        workflow_id=workflow.id,
        nodes=[
            {
                "node_key": "root",
                "target_agent_id": str(agent.id),
                "objective": "run root",
                "input_payload": {},
                "expected_output_contract": "done",
            }
        ],
        edges=[],
        source_kind=WorkflowRevisionSourceKind.OPERATOR,
    )
    ActivateWorkflowRevision(factory, clock).execute(
        workflow_id=workflow.id, revision_id=workflow_revision.id
    )
    task = Task.new(id=TaskId.new(), title="root", description="", created_at=AT)
    task.start(AT)
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=AT)
    run.start(AT)
    with factory() as uow:
        uow.tasks.add(task)
        uow.runs.add(run)
        uow.commit()
    return task, run, agent, workflow, workflow_revision


def test_workflow_execution_and_node_round_trip_preserves_frozen_provenance(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    task, run, agent, workflow, revision = _seed(factory)
    node = revision.nodes[0]
    agent_revision_id = active_agent_revision_id(factory, agent.id)
    execution = WorkflowExecution(
        id=WorkflowExecutionId.new(),
        root_run_id=run.id,
        workflow_id=workflow.id,
        workflow_revision_id=revision.id,
        workflow_content_sha256=revision.content_sha256,
        status=WorkflowExecutionStatus.RUNNING,
        started_at=AT,
    )
    node_execution = WorkflowNodeExecution(
        id=WorkflowNodeExecutionId.new(),
        workflow_execution_id=execution.id,
        workflow_node_id=node.id,
        workflow_revision_id=revision.id,
        node_key=node.node_key,
        target_agent_id=agent.id,
        target_agent_revision_id=agent_revision_id,
        target_agent_revision_sha256=agent_revision_sha(factory, agent_revision_id),
        status=WorkflowNodeExecutionStatus.PENDING,
        created_at=AT,
    )
    resolution = RunWorkflowResolution(
        id=RunWorkflowResolutionId.new(),
        run_id=run.id,
        workflow_id=workflow.id,
        workflow_revision_id=revision.id,
        content_sha256=revision.content_sha256,
        resolved_at=AT,
    )
    binding = TaskWorkflowBinding.new(task_id=task.id, workflow_id=workflow.id, at=AT)
    run.wait_for_workflow(AT, execution.id)

    with factory() as uow:
        uow.task_workflow_bindings.bind(binding)
        uow.run_workflow_resolutions.create(resolution)
        uow.workflow_executions.create(execution)
        uow.workflow_node_executions.create(node_execution)
        uow.runs.save(run)
        uow.commit()

    with factory() as uow:
        assert uow.task_workflow_bindings.get_by_task_id(task.id) == binding
        assert uow.run_workflow_resolutions.get_by_run_id(run.id) == resolution
        loaded_execution = uow.workflow_executions.get(execution.id)
        loaded_node = uow.workflow_node_executions.get(node_execution.id)
        loaded_run = uow.runs.get(run.id)
        assert loaded_execution == execution
        assert loaded_node == node_execution
        assert loaded_run is not None and loaded_run.workflow_execution_id == execution.id


def agent_revision_sha(factory: UnitOfWorkFactory, revision_id: AgentRevisionId) -> str:
    with factory() as uow:
        revision = uow.agent_revisions.get(revision_id)
        assert revision is not None
        return revision.content_sha256


def test_unique_run_resolution_and_workflow_node_identity_are_database_enforced(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    task, run, agent, workflow, revision = _seed(factory)
    resolution = RunWorkflowResolution(
        RunWorkflowResolutionId.new(),
        run.id,
        workflow.id,
        revision.id,
        revision.content_sha256,
        AT,
    )
    execution = WorkflowExecution(
        WorkflowExecutionId.new(),
        run.id,
        workflow.id,
        revision.id,
        revision.content_sha256,
        WorkflowExecutionStatus.RUNNING,
        AT,
    )
    node = revision.nodes[0]
    node_execution = WorkflowNodeExecution(
        WorkflowNodeExecutionId.new(),
        execution.id,
        node.id,
        revision.id,
        node.node_key,
        agent.id,
        agent_revision_id := active_agent_revision_id(factory, agent.id),
        agent_revision_sha(factory, agent_revision_id),
        WorkflowNodeExecutionStatus.PENDING,
        AT,
    )
    with factory() as uow:
        uow.run_workflow_resolutions.create(resolution)
        uow.workflow_executions.create(execution)
        uow.workflow_node_executions.create(node_execution)
        uow.commit()

    duplicate_resolution = RunWorkflowResolution(
        RunWorkflowResolutionId.new(),
        run.id,
        workflow.id,
        revision.id,
        revision.content_sha256,
        AT,
    )
    duplicate_node = WorkflowNodeExecution(
        WorkflowNodeExecutionId.new(),
        execution.id,
        node.id,
        revision.id,
        node.node_key,
        agent.id,
        agent_revision_id,
        agent_revision_sha(factory, agent_revision_id),
        WorkflowNodeExecutionStatus.PENDING,
        AT,
    )
    with pytest.raises(EntityConflict), factory() as uow:
        uow.run_workflow_resolutions.create(duplicate_resolution)
        uow.commit()
    with pytest.raises(EntityConflict), factory() as uow:
        uow.workflow_node_executions.create(duplicate_node)
        uow.commit()


def active_agent_revision_id(factory: UnitOfWorkFactory, agent_id: object) -> AgentRevisionId:
    with factory() as uow:
        agent = uow.agents.get(agent_id)  # type: ignore[arg-type]
        assert agent is not None and agent.active_revision_id is not None
        return agent.active_revision_id


def test_workflow_resolution_rejects_stale_claim_and_accepts_current_claim(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    _, run, _, workflow, revision = _seed(factory)
    with factory() as uow:
        uow.work_queue.enqueue(run.id, AT, AT)
        uow.commit()
    with factory() as stale:
        assert stale.work_queue.try_claim(run.id, "worker-a", "token-a", AT, AT.replace(hour=13))
        item = stale.work_queue.get(run.id)
        assert item is not None
        generation = item.claim_generation
        stale.commit()
    now = AT.replace(hour=14)
    with factory() as current:
        assert current.work_queue.try_claim(
            run.id, "worker-b", "token-b", now, now.replace(hour=15)
        )
        current_item = current.work_queue.get(run.id)
        assert current_item is not None
        resolution = RunWorkflowResolution(
            RunWorkflowResolutionId.new(),
            run.id,
            workflow.id,
            revision.id,
            revision.content_sha256,
            now,
        )
        assert current.run_workflow_resolutions.add_if_claimed(
            resolution,
            "worker-b",
            "token-b",
            current_item.claim_generation,
            now,
        )
        current.commit()
    with factory() as stale_retry:
        assert not stale_retry.run_workflow_resolutions.add_if_claimed(
            resolution, "worker-a", "token-a", generation, now
        )
        stale_retry.commit()
    with factory() as uow:
        assert uow.run_workflow_resolutions.get_by_run_id(run.id) == resolution


def test_reconcile_rejects_raw_sql_node_provenance_corruption(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    task, run, agent, workflow, revision = _seed(factory)
    node = revision.nodes[0]
    agent_revision_id = active_agent_revision_id(factory, agent.id)
    execution = WorkflowExecution(
        id=WorkflowExecutionId.new(),
        root_run_id=run.id,
        workflow_id=workflow.id,
        workflow_revision_id=revision.id,
        workflow_content_sha256=revision.content_sha256,
        status=WorkflowExecutionStatus.RUNNING,
        started_at=AT,
    )
    node_execution = WorkflowNodeExecution(
        id=WorkflowNodeExecutionId.new(),
        workflow_execution_id=execution.id,
        workflow_node_id=node.id,
        workflow_revision_id=revision.id,
        node_key=node.node_key,
        target_agent_id=agent.id,
        target_agent_revision_id=agent_revision_id,
        target_agent_revision_sha256=agent_revision_sha(factory, agent_revision_id),
        status=WorkflowNodeExecutionStatus.PENDING,
        created_at=AT,
    )
    resolution = RunWorkflowResolution(
        id=RunWorkflowResolutionId.new(),
        run_id=run.id,
        workflow_id=workflow.id,
        workflow_revision_id=revision.id,
        content_sha256=revision.content_sha256,
        resolved_at=AT,
    )
    run.wait_for_workflow(AT, execution.id)
    with factory() as uow:
        uow.run_workflow_resolutions.create(resolution)
        uow.workflow_executions.create(execution)
        uow.workflow_node_executions.create(node_execution)
        uow.runs.save(run)
        uow.commit()
    engine = create_engine(f"sqlite:///{tmp_path / 'workflow-execution.db'}")
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE workflow_node_executions SET node_key='corrupt' WHERE id=:id"),
            {"id": str(node_execution.id)},
        )
    from friday.application.errors import WorkflowIntegrityError

    with pytest.raises(WorkflowIntegrityError):
        ReconcileWorkflowExecution(factory, _Clock()).execute(execution.id)


def test_reconcile_rejects_raw_sql_agent_revision_snapshot_corruption(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    task, run, agent, workflow, revision = _seed(factory)
    node = revision.nodes[0]
    agent_revision_id = active_agent_revision_id(factory, agent.id)
    execution = WorkflowExecution(
        id=WorkflowExecutionId.new(),
        root_run_id=run.id,
        workflow_id=workflow.id,
        workflow_revision_id=revision.id,
        workflow_content_sha256=revision.content_sha256,
        status=WorkflowExecutionStatus.RUNNING,
        started_at=AT,
    )
    node_execution = WorkflowNodeExecution(
        id=WorkflowNodeExecutionId.new(),
        workflow_execution_id=execution.id,
        workflow_node_id=node.id,
        workflow_revision_id=revision.id,
        node_key=node.node_key,
        target_agent_id=agent.id,
        target_agent_revision_id=agent_revision_id,
        target_agent_revision_sha256=agent_revision_sha(factory, agent_revision_id),
        status=WorkflowNodeExecutionStatus.PENDING,
        created_at=AT,
    )
    resolution = RunWorkflowResolution(
        id=RunWorkflowResolutionId.new(),
        run_id=run.id,
        workflow_id=workflow.id,
        workflow_revision_id=revision.id,
        content_sha256=revision.content_sha256,
        resolved_at=AT,
    )
    run.wait_for_workflow(AT, execution.id)
    with factory() as uow:
        uow.run_workflow_resolutions.create(resolution)
        uow.workflow_executions.create(execution)
        uow.workflow_node_executions.create(node_execution)
        uow.runs.save(run)
        uow.commit()

    # Corrupt the frozen Agent-revision snapshot in place: the node
    # execution's recorded target_agent_revision_id no longer matches the
    # sha256 it froze, so the immutable Agent snapshot proof must fail
    # closed instead of trusting a tampered row.
    engine = create_engine(f"sqlite:///{tmp_path / 'workflow-execution.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workflow_node_executions SET target_agent_revision_sha256=:sha WHERE id=:id"
            ),
            {"sha": "b" * 64, "id": str(node_execution.id)},
        )

    from friday.application.errors import WorkflowIntegrityError

    with pytest.raises(WorkflowIntegrityError):
        ReconcileWorkflowExecution(factory, _Clock()).execute(execution.id)


def test_sqlite_terminal_publication_is_idempotent(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    task, run, _, workflow, _ = _seed(factory)

    with factory() as uow:
        uow.task_workflow_bindings.bind(
            TaskWorkflowBinding.new(task_id=task.id, workflow_id=workflow.id, at=AT)
        )
        uow.work_queue.enqueue(run.id, AT, AT)
        uow.commit()

    with factory() as uow:
        assert uow.work_queue.try_claim(run.id, "worker-a", "token-a", AT, AT.replace(hour=13))
        item = uow.work_queue.get(run.id)
        assert item is not None
        claim_generation = item.claim_generation
        uow.commit()

    execution = StartWorkflowExecution(factory, _Clock(), _runtime_registry()).execute(
        run.id, workflow.id, "worker-a", "token-a", claim_generation
    )

    with factory() as uow:
        nodes = uow.workflow_node_executions.list_by_execution(execution.id)
        assert len(nodes) == 1
        child_run_id = nodes[0].child_run_id
        assert child_run_id is not None

    # Complete the real dispatched child in a separate transaction.  This
    # proves the reconciler observes durable state rather than a process-local
    # mutation or synthetic foreign-key values.
    with factory() as uow:
        child_run = uow.runs.get(child_run_id)
        assert child_run is not None
        child_run.start(AT)
        child_run.succeed(AT)
        uow.runs.save(child_run)
        uow.commit()

    first = ReconcileWorkflowExecution(factory, _Clock()).execute(execution.id)
    second = ReconcileWorkflowExecution(factory, _Clock()).execute(execution.id)

    assert first[0].status is WorkflowNodeExecutionStatus.SUCCEEDED
    assert second[0].status is WorkflowNodeExecutionStatus.SUCCEEDED
    with factory() as uow:
        loaded_execution = uow.workflow_executions.get(execution.id)
        loaded_run = uow.runs.get(run.id)
        assert loaded_execution is not None
        assert loaded_run is not None
        assert loaded_execution.status is WorkflowExecutionStatus.SUCCEEDED
        assert loaded_run.status is RunStatus.SUCCEEDED


def _dispatch_single_node_workflow(
    factory: UnitOfWorkFactory,
) -> tuple[Task, Run, Workflow, WorkflowExecution, RunId]:
    task, run, _, workflow, _ = _seed(factory)
    with factory() as uow:
        uow.task_workflow_bindings.bind(
            TaskWorkflowBinding.new(task_id=task.id, workflow_id=workflow.id, at=AT)
        )
        uow.work_queue.enqueue(run.id, AT, AT)
        uow.commit()
    with factory() as uow:
        assert uow.work_queue.try_claim(run.id, "worker-a", "token-a", AT, AT.replace(hour=13))
        item = uow.work_queue.get(run.id)
        assert item is not None
        claim_generation = item.claim_generation
        uow.commit()
    execution = StartWorkflowExecution(factory, _Clock(), _runtime_registry()).execute(
        run.id, workflow.id, "worker-a", "token-a", claim_generation
    )
    with factory() as uow:
        nodes = uow.workflow_node_executions.list_by_execution(execution.id)
        assert len(nodes) == 1
        child_run_id = nodes[0].child_run_id
        assert child_run_id is not None
    return task, run, workflow, execution, child_run_id


def test_manual_retry_of_workflow_owned_child_execution_is_rejected(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    _, _, _, _, child_run_id = _dispatch_single_node_workflow(factory)

    with factory() as uow:
        child_run = uow.runs.get(child_run_id)
        assert child_run is not None
        child_run.start(AT)
        child_run.fail(
            AT, Failure("worker_timeout", "worker timed out", True, FailureCause.TIMEOUT)
        )
        uow.runs.save(child_run)
        uow.commit()

    with factory() as uow:
        before_runs = [(r.id, r.status) for r in uow.runs.list_for_execution(child_run_id)]
        before_queue = uow.work_queue.get(child_run_id)
        before_node = uow.workflow_node_executions.get_by_child_execution_id(child_run_id)
        assert before_node is not None
        before_node_status = before_node.status

    with pytest.raises(
        WorkflowNodeManualRetryForbidden, match="workflow_node_manual_retry_forbidden"
    ):
        RetryFailedRun(factory, _Clock()).execute(RetryFailedRunCommand(child_run_id))

    with factory() as uow:
        after_runs = [(r.id, r.status) for r in uow.runs.list_for_execution(child_run_id)]
        after_queue = uow.work_queue.get(child_run_id)
        after_node = uow.workflow_node_executions.get_by_child_execution_id(child_run_id)
        assert after_runs == before_runs
        assert after_queue == before_queue
        assert after_node is not None and after_node.status == before_node_status


def test_manual_cancel_of_active_workflow_root_is_rejected(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    _, run, _, execution, child_run_id = _dispatch_single_node_workflow(factory)

    with factory() as uow:
        before_run = uow.runs.get(run.id)
        assert before_run is not None
        before_status = before_run.status
        before_execution_id = before_run.workflow_execution_id
        before_child = uow.runs.get(child_run_id)
        assert before_child is not None
        before_child_status = before_child.status

    with pytest.raises(
        WorkflowCancelNotSupportedWhileActive,
        match="workflow_cancel_not_supported_while_active",
    ):
        CancelRun(factory, _Clock()).execute(CancelRunCommand(run.id))

    with factory() as uow:
        after_run = uow.runs.get(run.id)
        assert after_run is not None
        assert after_run.status == before_status == RunStatus.WAITING_FOR_WORKFLOW
        assert after_run.workflow_execution_id == before_execution_id
        after_child = uow.runs.get(child_run_id)
        assert after_child is not None
        assert after_child.status == before_child_status
        loaded_execution = uow.workflow_executions.get(execution.id)
        assert loaded_execution is not None
        assert loaded_execution.status is WorkflowExecutionStatus.RUNNING


def test_child_cancellation_terminalizes_workflow_without_parent_propagation(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    _, run, _, execution, child_run_id = _dispatch_single_node_workflow(factory)

    with factory() as uow:
        child_run = uow.runs.get(child_run_id)
        assert child_run is not None
        child_run.start(AT)
        child_run.cancel(AT)
        uow.runs.save(child_run)
        uow.commit()

    nodes = ReconcileWorkflowExecution(factory, _Clock()).execute(execution.id)

    assert nodes[0].status is WorkflowNodeExecutionStatus.CANCELLED
    with factory() as uow:
        loaded_execution = uow.workflow_executions.get(execution.id)
        loaded_run = uow.runs.get(run.id)
        assert loaded_execution is not None
        assert loaded_run is not None
        assert loaded_execution.status is WorkflowExecutionStatus.CANCELLED
        assert loaded_run.status is RunStatus.CANCELLED


def test_root_workflow_result_is_deterministic_durable_and_authority_free(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    _, run, workflow, execution, child_run_id = _dispatch_single_node_workflow(factory)

    with factory() as uow:
        child_run = uow.runs.get(child_run_id)
        assert child_run is not None
        child_run.start(AT)
        child_run.succeed(AT)
        uow.runs.save(child_run)
        uow.commit()

    ReconcileWorkflowExecution(factory, _Clock()).execute(execution.id)

    # Simulate a process restart: open a brand-new UnitOfWorkFactory against
    # the same sqlite file instead of reusing the one already in scope.
    restarted_factory = create_unit_of_work_factory(
        create_session_factory(create_engine(f"sqlite:///{tmp_path / 'workflow-execution.db'}"))
    )
    with restarted_factory() as uow:
        events = uow.events.list_for_run(run.id)
        succeeded = next(
            event for event in events if event.type is RunEventType.WORKFLOW_EXECUTION_SUCCEEDED
        )
        result = succeeded.payload
        assert isinstance(result, dict)
        assert result["workflow_id"] == str(workflow.id)
        assert result["workflow_key"] == workflow.key
        assert result["workflow_execution_id"] == str(execution.id)
        nodes = result["nodes"]
        assert isinstance(nodes, dict)
        assert set(nodes.keys()) == {"root"}
        root_result = nodes["root"]
        assert isinstance(root_result, dict)
        assert root_result["child_run_id"] == str(child_run_id)

        # Re-running reconciliation must not emit a second success event.
        ReconcileWorkflowExecution(restarted_factory, _Clock()).execute(execution.id)
        assert (
            len(
                [
                    event
                    for event in uow.events.list_for_run(run.id)
                    if event.type is RunEventType.WORKFLOW_EXECUTION_SUCCEEDED
                ]
            )
            == 1
        )

        assert uow.approvals.list_for_run(run.id) == []
        assert uow.tool_invocations.list_for_run(run.id) == []
