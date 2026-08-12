"""SQLite proofs for the durable Workflow execution/scheduler boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from friday.application.agent_registry import (
    ActivateAgentRevision,
    CreateAgent,
    CreateAgentRevision,
)
from friday.application.brain_runtime_registry import BrainRuntimeRegistry
from friday.application.errors import EntityConflict
from friday.application.ports import UnitOfWorkFactory
from friday.application.workflow_registry import (
    ActivateWorkflowRevision,
    CreateWorkflow,
    CreateWorkflowRevision,
)
from friday.domain import (
    Agent,
    AgentRevisionSourceKind,
    Run,
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
