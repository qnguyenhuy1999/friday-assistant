from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from friday.application.agent_registry import ReplaceTaskAgent
from friday.application.brain_runtime_registry import BrainRuntimeRegistry
from friday.application.errors import (
    ClaimLost,
    WorkflowBindingError,
    WorkflowExecutionError,
    WorkflowNotFound,
)
from friday.application.workflow_execution_use_cases import (
    BindTaskWorkflow,
    ReconcileWorkflowExecution,
    ResolveRunWorkflow,
    StartWorkflowExecution,
)
from friday.domain import (
    Agent,
    AgentId,
    AgentRevision,
    AgentRevisionId,
    AgentRevisionSourceKind,
    Run,
    RunId,
    RunStatus,
    Task,
    TaskAgentBinding,
    TaskId,
    Workflow,
    WorkflowEdge,
    WorkflowEdgeId,
    WorkflowExecution,
    WorkflowExecutionStatus,
    WorkflowId,
    WorkflowNode,
    WorkflowNodeExecutionStatus,
    WorkflowNodeId,
    WorkflowRevision,
    WorkflowRevisionId,
    WorkflowRevisionSourceKind,
)
from tests.application.fakes import CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = T0 + timedelta(minutes=1)
WORKER = "workflow-worker"
TOKEN = "workflow-token"


def _runtime_registry() -> BrainRuntimeRegistry:
    registry = BrainRuntimeRegistry()
    registry.register("claude_cli", lambda: None)  # type: ignore[arg-type,return-value]
    return registry


def _agent(uow: FakeUnitOfWork, *, key: str, activate: bool = True) -> Agent:
    agent = Agent.new(id=AgentId.new(), key=key, display_name=key, description="", created_at=T0)
    uow.agents.add(agent)
    if activate:
        revision = AgentRevision.new(
            id=AgentRevisionId.new(),
            agent_id=agent.id,
            version=1,
            instructions=f"instructions for {key}",
            runtime_kind="claude_cli",
            runtime_config={},
            source_kind=AgentRevisionSourceKind.OPERATOR,
            created_at=T0,
        )
        uow.agent_revisions.add(revision)
        agent.activate(revision, T0)
        uow.agents.save(agent)
    return agent


def _workflow(
    uow: FakeUnitOfWork,
    agents: list[Agent],
    edges: list[tuple[int, int]],
) -> tuple[Workflow, WorkflowRevision, list[WorkflowNode]]:
    workflow = Workflow.new(
        id=WorkflowId.new(),
        key=f"workflow.{str(WorkflowId.new())[:8]}",
        display_name="Workflow",
        description="",
        created_at=T0,
    )
    uow.workflows.add(workflow)
    revision_id = WorkflowRevisionId.new()
    nodes = [
        WorkflowNode(
            id=WorkflowNodeId.new(),
            revision_id=revision_id,
            node_key=f"node-{index}",
            target_agent_id=agents[index % len(agents)].id,
            objective=f"objective-{index}",
            input_payload={"index": index},
            expected_output_contract="result",
            created_at=T0,
        )
        for index in range(max(len(agents), max((max(edge) for edge in edges), default=-1) + 1))
    ]
    workflow_edges = [
        WorkflowEdge(
            id=WorkflowEdgeId.new(),
            revision_id=revision_id,
            from_node_id=nodes[source].id,
            to_node_id=nodes[target].id,
            created_at=T0,
        )
        for source, target in edges
    ]
    revision = WorkflowRevision.new(
        id=revision_id,
        workflow_id=workflow.id,
        version=1,
        nodes=nodes,
        edges=workflow_edges,
        source_kind=WorkflowRevisionSourceKind.OPERATOR,
        created_at=T0,
    )
    uow.workflow_revisions.add(revision)
    workflow.activate(revision, T0)
    uow.workflows.save(workflow)
    return workflow, revision, nodes


def _root(
    uow: FakeUnitOfWork,
    factory: CountingUnitOfWorkFactory,
    workflow: Workflow,
    *,
    direct_agent: Agent | None = None,
) -> tuple[Task, Run, int]:
    task = Task.new(id=TaskId.new(), title="root", description="root", created_at=T0)
    task.start(T0)
    uow.tasks.add(task)
    if direct_agent is not None:
        uow.task_agent_bindings.replace(task.id, TaskAgentBinding(task.id, direct_agent.id, T0))
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
    run.start(T0)
    uow.runs.add(run)
    uow.work_queue.enqueue(run.id, T0, T0)
    assert uow.work_queue.try_claim(run.id, WORKER, TOKEN, T0, T0 + timedelta(hours=1))
    if direct_agent is None:
        BindTaskWorkflow(factory, FakeClock(T0)).execute(task_id=task.id, workflow_id=workflow.id)
    item = uow.work_queue.get(run.id)
    assert item is not None
    return task, run, item.claim_generation


def _bootstrap(
    *,
    edges: list[tuple[int, int]],
    agent_count: int = 1,
    activate_all: bool = True,
) -> tuple[
    FakeUnitOfWork,
    CountingUnitOfWorkFactory,
    FakeClock,
    Workflow,
    WorkflowRevision,
    list[WorkflowNode],
    Task,
    Run,
    WorkflowExecution,
]:
    uow = FakeUnitOfWork()
    clock = FakeClock(T0)
    factory = CountingUnitOfWorkFactory(uow)
    agents = [
        _agent(uow, key=f"agent-{index}", activate=activate_all or index == 0)
        for index in range(agent_count)
    ]
    workflow, revision, nodes = _workflow(uow, agents, edges)
    task, run, generation = _root(uow, factory, workflow)
    execution = StartWorkflowExecution(factory, clock, _runtime_registry()).execute(
        run.id, workflow.id, WORKER, TOKEN, generation
    )
    return uow, factory, clock, workflow, revision, nodes, task, run, execution


def _complete_child(uow: FakeUnitOfWork, child_run_id: RunId) -> None:
    child = uow.runs.get(child_run_id)
    assert child is not None
    child.start(T0)
    child.succeed(T1)
    uow.runs.save(child)


def test_binding_rejects_direct_agent_and_archived_workflows() -> None:
    uow = FakeUnitOfWork()
    factory = CountingUnitOfWorkFactory(uow)
    clock = FakeClock(T0)
    agent = _agent(uow, key="direct")
    workflow, _, _ = _workflow(uow, [agent], [])
    task = Task.new(id=TaskId.new(), title="task", description="", created_at=T0)
    uow.tasks.add(task)
    uow.task_agent_bindings.replace(task.id, TaskAgentBinding(task.id, agent.id, T0))

    with pytest.raises(WorkflowBindingError):
        BindTaskWorkflow(factory, clock).execute(task_id=task.id, workflow_id=workflow.id)

    uow.task_agent_bindings.replace(task.id, None)
    workflow.archive(T1)
    with pytest.raises(WorkflowBindingError):
        BindTaskWorkflow(factory, clock).execute(task_id=task.id, workflow_id=workflow.id)


def test_replace_task_agent_rejects_workflow_owned_task() -> None:
    uow = FakeUnitOfWork()
    factory = CountingUnitOfWorkFactory(uow)
    clock = FakeClock(T0)
    first = _agent(uow, key="first")
    second = _agent(uow, key="second")
    workflow, _, _ = _workflow(uow, [first], [])
    task = Task.new(id=TaskId.new(), title="task", description="", created_at=T0)
    uow.tasks.add(task)
    BindTaskWorkflow(factory, clock).execute(task_id=task.id, workflow_id=workflow.id)

    with pytest.raises(WorkflowBindingError):
        ReplaceTaskAgent(factory, clock).execute(task_id=task.id, agent_id=second.id)


def test_run_workflow_resolution_is_immutable_after_revision_change() -> None:
    uow = FakeUnitOfWork()
    factory = CountingUnitOfWorkFactory(uow)
    clock = FakeClock(T0)
    agent = _agent(uow, key="resolver")
    workflow, revision, nodes = _workflow(uow, [agent], [])
    _, run, generation = _root(uow, factory, workflow)
    resolver = ResolveRunWorkflow(factory, clock)
    first = resolver.execute(run.id, workflow.id, WORKER, TOKEN, generation)

    second_revision_id = WorkflowRevisionId.new()
    second_node = WorkflowNode(
        id=WorkflowNodeId.new(),
        revision_id=second_revision_id,
        node_key="replacement",
        target_agent_id=agent.id,
        objective="replacement",
        input_payload={},
        expected_output_contract="result",
        created_at=T1,
    )
    second_revision = WorkflowRevision.new(
        id=second_revision_id,
        workflow_id=workflow.id,
        version=2,
        nodes=[second_node],
        edges=[],
        source_kind=WorkflowRevisionSourceKind.OPERATOR,
        created_at=T1,
    )
    uow.workflow_revisions.add(second_revision)
    workflow.activate(second_revision, T1)
    uow.workflows.save(workflow)

    again = resolver.execute(run.id, workflow.id, WORKER, TOKEN, generation)
    assert again == first
    assert again.workflow_revision_id == revision.id
    assert nodes[0].node_key != second_node.node_key


def test_start_freezes_exact_agent_revisions_and_dispatches_only_roots() -> None:
    uow, factory, clock, workflow, revision, _, task, run, execution = _bootstrap(
        edges=[(0, 1)], agent_count=1
    )
    node_executions = uow.workflow_node_executions.list_by_execution(execution.id)
    assert run.status is RunStatus.WAITING_FOR_WORKFLOW
    assert run.workflow_execution_id == execution.id
    assert execution.workflow_revision_id == revision.id
    assert len(node_executions) == 2
    assert [node.status for node in node_executions] == [
        WorkflowNodeExecutionStatus.DISPATCHED,
        WorkflowNodeExecutionStatus.PENDING,
    ]
    assert len(uow.task_repo.items) == 2
    assert len(uow.run_repo.items) == 2
    assert uow.work_queue.get(run.id) is None
    assert task.id in uow.task_repo.items

    old_revision_ids = {node.target_agent_revision_id for node in node_executions}
    agent = next(iter(uow.agent_repo.items.values()))
    replacement = AgentRevision.new(
        id=AgentRevisionId.new(),
        agent_id=agent.id,
        version=2,
        instructions="replacement",
        runtime_kind="claude_cli",
        runtime_config={},
        source_kind=AgentRevisionSourceKind.OPERATOR,
        created_at=T1,
    )
    uow.agent_revisions.add(replacement)
    agent.activate(replacement, T1)
    uow.agents.save(agent)
    assert old_revision_ids == {node.target_agent_revision_id for node in node_executions}
    assert clock.now() == T0


def test_start_fails_before_any_execution_state_when_agent_snapshot_is_invalid() -> None:
    uow = FakeUnitOfWork()
    factory = CountingUnitOfWorkFactory(uow)
    clock = FakeClock(T0)
    valid = _agent(uow, key="valid")
    invalid = _agent(uow, key="invalid", activate=False)
    workflow, _, _ = _workflow(uow, [valid, invalid], [(0, 1)])
    task, run, generation = _root(uow, factory, workflow)

    with pytest.raises(WorkflowExecutionError):
        StartWorkflowExecution(factory, clock, _runtime_registry()).execute(
            run.id, workflow.id, WORKER, TOKEN, generation
        )

    assert not uow.workflow_executions.items
    assert not uow.workflow_node_executions.items
    assert not uow.run_workflow_resolutions.items
    assert len(uow.task_repo.items) == 1
    assert len(uow.run_repo.items) == 1
    assert run.status is RunStatus.RUNNING
    assert task.id in uow.task_repo.items


def test_reconcile_chain_to_terminal_execution() -> None:
    uow, factory, clock, workflow, _, _, _, run, execution = _bootstrap(edges=[(0, 1)])
    first = next(
        node
        for node in uow.workflow_node_executions.list_by_execution(execution.id)
        if node.node_key == "node-0"
    )
    _complete_child(uow, first.child_run_id)  # type: ignore[arg-type]
    reconciled = ReconcileWorkflowExecution(factory, clock).execute(execution.id)
    second = next(node for node in reconciled if node.node_key == "node-1")
    assert second.status is WorkflowNodeExecutionStatus.DISPATCHED

    _complete_child(uow, second.child_run_id)  # type: ignore[arg-type]
    reconciled = ReconcileWorkflowExecution(factory, clock).execute(execution.id)
    assert all(node.status is WorkflowNodeExecutionStatus.SUCCEEDED for node in reconciled)
    completed_execution = uow.workflow_executions.get(execution.id)
    completed_run = uow.runs.get(run.id)
    assert completed_execution is not None
    assert completed_run is not None
    assert completed_execution.status is WorkflowExecutionStatus.SUCCEEDED
    assert completed_run.status is RunStatus.SUCCEEDED


def test_reconcile_multi_root_dispatches_all_roots() -> None:
    uow, factory, clock, _, _, _, _, _, execution = _bootstrap(edges=[], agent_count=3)
    nodes = uow.workflow_node_executions.list_by_execution(execution.id)
    assert len(nodes) == 3
    assert all(node.status is WorkflowNodeExecutionStatus.DISPATCHED for node in nodes)
    assert len(uow.work_queue_repo.items) == 3


def test_reconcile_fan_out_and_fan_in_waits_for_all_predecessors() -> None:
    uow, factory, clock, _, _, _, _, _, execution = _bootstrap(
        edges=[(0, 1), (0, 2), (1, 3), (2, 3)]
    )
    nodes = uow.workflow_node_executions.list_by_execution(execution.id)
    root = next(node for node in nodes if node.node_key == "node-0")
    _complete_child(uow, root.child_run_id)  # type: ignore[arg-type]
    nodes = ReconcileWorkflowExecution(factory, clock).execute(execution.id)
    branches = [node for node in nodes if node.node_key in {"node-1", "node-2"}]
    join = next(node for node in nodes if node.node_key == "node-3")
    assert all(node.status is WorkflowNodeExecutionStatus.DISPATCHED for node in branches)
    assert join.status is WorkflowNodeExecutionStatus.PENDING

    _complete_child(uow, branches[0].child_run_id)  # type: ignore[arg-type]
    ReconcileWorkflowExecution(factory, clock).execute(execution.id)
    assert uow.workflow_node_executions.items[join.id].status is WorkflowNodeExecutionStatus.PENDING

    _complete_child(uow, branches[1].child_run_id)  # type: ignore[arg-type]
    nodes = ReconcileWorkflowExecution(factory, clock).execute(execution.id)
    join = next(node for node in nodes if node.node_key == "node-3")
    assert join.status is WorkflowNodeExecutionStatus.DISPATCHED


def test_failed_predecessor_blocks_downstream_nodes() -> None:
    uow, factory, clock, _, _, _, _, _, execution = _bootstrap(edges=[(0, 1), (1, 2)])
    first = next(
        node
        for node in uow.workflow_node_executions.list_by_execution(execution.id)
        if node.node_key == "node-0"
    )
    child = uow.runs.get(first.child_run_id)  # type: ignore[arg-type]
    assert child is not None
    child.start(T0)
    from friday.domain import Failure, FailureCause

    child.fail(T1, Failure("failed", "no", False, FailureCause.RUNTIME))
    uow.runs.save(child)
    nodes = ReconcileWorkflowExecution(factory, clock).execute(execution.id)
    assert (
        next(node for node in nodes if node.node_key == "node-1").status
        is WorkflowNodeExecutionStatus.BLOCKED
    )
    assert (
        next(node for node in nodes if node.node_key == "node-2").status
        is WorkflowNodeExecutionStatus.BLOCKED
    )


def test_oversized_predecessor_result_blocks_dependent_before_child_creation() -> None:
    from typing import cast

    from friday.domain.json_value import JsonValue

    uow, factory, clock, _, _, _, _, run, execution = _bootstrap(edges=[(0, 1)])
    nodes = uow.workflow_node_executions.list_by_execution(execution.id)
    first = next(node for node in nodes if node.node_key == "node-0")
    child = uow.runs.get(first.child_run_id)  # type: ignore[arg-type]
    assert child is not None
    child.start(T0)
    child.succeed(T1)
    uow.runs.save(child)
    # An oversized durable predecessor result (per-result bound is exceeded)
    # must fail the dependent node closed at dispatch time -- never truncated.
    first.succeed(T1, cast(JsonValue, {"blob": "x" * 4000}))
    reconciled = ReconcileWorkflowExecution(factory, clock).execute(execution.id)
    dependent = next(node for node in reconciled if node.node_key == "node-1")
    assert dependent.status is WorkflowNodeExecutionStatus.BLOCKED
    assert dependent.failure_code == "workflow_context_too_large"
    assert dependent.child_task_id is None
    assert dependent.child_run_id is None
    assert dependent.child_execution_id is None
    # Only node-0's own child remains queued — the dependent was never
    # dispatched, so no BrainRuntime execution could have been started.
    assert len(uow.work_queue_repo.items) == 1
    assert not any(
        task.title.startswith("Workflow: node-1") for task in uow.task_repo.items.values()
    )
    completed = uow.workflow_executions.get(execution.id)
    assert completed is not None
    assert completed.status is WorkflowExecutionStatus.FAILED
    assert completed.failure_code == "workflow_context_too_large"
    assert uow.runs.get(run.id).status is RunStatus.FAILED  # type: ignore[union-attr]


def test_aggregate_predecessor_context_over_bound_blocks_dependent() -> None:
    from typing import cast

    from friday.application.workflow_execution_use_cases import _persist_node
    from friday.domain.json_value import JsonValue

    uow, factory, clock, _, _, _, _, run, execution = _bootstrap(
        edges=[(0, 1), (0, 2), (0, 3), (1, 4), (2, 4), (3, 4)], agent_count=5
    )
    nodes = uow.workflow_node_executions.list_by_execution(execution.id)
    root = next(node for node in nodes if node.node_key == "node-0")
    child = uow.runs.get(root.child_run_id)  # type: ignore[arg-type]
    assert child is not None
    child.start(T0)
    child.succeed(T1)
    uow.runs.save(child)
    nodes = ReconcileWorkflowExecution(factory, clock).execute(execution.id)
    branches = [node for node in nodes if node.node_key in {"node-1", "node-2", "node-3"}]
    for node in branches:
        payload = cast(JsonValue, {"branch": node.node_key, "payload": "y" * 1900})
        assert len(__import__("json").dumps(payload, sort_keys=True, separators=(",", ":"))) <= 2000
        node.succeed(T1, payload)
        _persist_node(uow, node)
    reconciled = ReconcileWorkflowExecution(factory, clock).execute(execution.id)
    join = next(node for node in reconciled if node.node_key == "node-4")
    assert join.status is WorkflowNodeExecutionStatus.BLOCKED
    assert join.failure_code == "workflow_context_too_large"
    assert join.child_task_id is None and join.child_run_id is None
    completed = uow.workflow_executions.get(execution.id)
    assert completed is not None
    assert completed.status is WorkflowExecutionStatus.FAILED
    assert completed.failure_code == "workflow_context_too_large"
    assert uow.runs.get(run.id).status is RunStatus.FAILED  # type: ignore[union-attr]


def test_reconcile_failed_child_fails_root_and_execution() -> None:
    uow, factory, clock, _, _, _, _, run, execution = _bootstrap(edges=[(0, 1)])
    first = next(
        node
        for node in uow.workflow_node_executions.list_by_execution(execution.id)
        if node.node_key == "node-0"
    )
    child = uow.runs.get(first.child_run_id)  # type: ignore[arg-type]
    assert child is not None
    child.start(T0)
    child.fail(
        T1,
        __import__("friday.domain", fromlist=["Failure"]).Failure(
            "child_failed",
            "failure",
            False,
            __import__("friday.domain", fromlist=["FailureCause"]).FailureCause.RUNTIME,
        ),
    )
    uow.runs.save(child)
    nodes = ReconcileWorkflowExecution(factory, clock).execute(execution.id)
    assert (
        next(n for n in nodes if n.node_key == "node-0").status
        is WorkflowNodeExecutionStatus.FAILED
    )
    assert (
        next(n for n in nodes if n.node_key == "node-1").status
        is WorkflowNodeExecutionStatus.BLOCKED
    )
    assert uow.workflow_executions.get(execution.id).status is WorkflowExecutionStatus.FAILED  # type: ignore[union-attr]
    assert uow.runs.get(run.id).status is RunStatus.FAILED  # type: ignore[union-attr]


def test_reconcile_cancelled_child_cancels_root_and_execution() -> None:
    uow, factory, clock, _, _, _, _, run, execution = _bootstrap(edges=[])
    node = uow.workflow_node_executions.list_by_execution(execution.id)[0]
    child = uow.runs.get(node.child_run_id)  # type: ignore[arg-type]
    assert child is not None
    child.cancel(T1)
    uow.runs.save(child)
    ReconcileWorkflowExecution(factory, clock).execute(execution.id)
    assert uow.workflow_executions.get(execution.id).status is WorkflowExecutionStatus.CANCELLED  # type: ignore[union-attr]
    assert uow.runs.get(run.id).status is RunStatus.CANCELLED  # type: ignore[union-attr]


def test_reconcile_success_is_idempotent_and_root_result_is_deterministic() -> None:
    uow, factory, clock, _, _, _, _, run, execution = _bootstrap(edges=[])
    node = uow.workflow_node_executions.list_by_execution(execution.id)[0]
    child = uow.runs.get(node.child_run_id)  # type: ignore[arg-type]
    assert child is not None
    child.start(T0)
    child.succeed(T1)
    uow.runs.save(child)
    ReconcileWorkflowExecution(factory, clock).execute(execution.id)
    before = list(uow.events.list_for_run(run.id))
    ReconcileWorkflowExecution(factory, clock).execute(execution.id)
    after = list(uow.events.list_for_run(run.id))
    assert len(after) == len(before)
    from friday.domain.event import RunEventType

    succeeded = [e for e in after if e.type is RunEventType.WORKFLOW_EXECUTION_SUCCEEDED]
    assert len(succeeded) == 1
    assert isinstance(succeeded[0].payload, dict)
    assert succeeded[0].payload["workflow_execution_id"] == str(execution.id)


def test_start_workflow_execution_is_idempotent_for_existing_execution() -> None:
    uow, factory, _, workflow, _, _, _, root_run, execution = _bootstrap(edges=[])
    result = StartWorkflowExecution(
        uow_factory=factory,
        clock=FakeClock(T1),
        runtime_registry=_runtime_registry(),
    ).execute(
        root_run_id=root_run.id,
        workflow_id=workflow.id,
        worker_id="worker-1",
        claim_token="claim-token",
        claim_generation=1,
    )
    assert result.id == execution.id
    assert result.workflow_revision_id == execution.workflow_revision_id


def test_start_fails_closed_when_node_targets_an_unregistered_agent() -> None:
    uow = FakeUnitOfWork()
    factory = CountingUnitOfWorkFactory(uow)
    clock = FakeClock(T0)
    # Build a Workflow revision whose node targets an AgentId that was never
    # added to the Agent registry -- this must fail closed rather than
    # dispatch a node with no resolvable authority.
    missing_agent_id = AgentId.new()
    workflow = Workflow.new(
        id=WorkflowId.new(),
        key="workflow.missing-agent",
        display_name="Missing Agent Target",
        description="",
        created_at=T0,
    )
    uow.workflows.add(workflow)
    revision_id = WorkflowRevisionId.new()
    node = WorkflowNode(
        id=WorkflowNodeId.new(),
        revision_id=revision_id,
        node_key="node-0",
        target_agent_id=missing_agent_id,
        objective="objective",
        input_payload={},
        expected_output_contract="result",
        created_at=T0,
    )
    revision = WorkflowRevision.new(
        id=revision_id,
        workflow_id=workflow.id,
        version=1,
        nodes=[node],
        edges=[],
        source_kind=WorkflowRevisionSourceKind.OPERATOR,
        created_at=T0,
    )
    uow.workflow_revisions.add(revision)
    workflow.activate(revision, T0)
    uow.workflows.save(workflow)
    task, run, generation = _root(uow, factory, workflow)

    with pytest.raises(WorkflowExecutionError, match="target Agent is missing"):
        StartWorkflowExecution(factory, clock, _runtime_registry()).execute(
            run.id, workflow.id, WORKER, TOKEN, generation
        )
    assert not uow.workflow_executions.items


def test_start_fails_closed_for_archived_workflow_resolved_after_binding() -> None:
    uow = FakeUnitOfWork()
    factory = CountingUnitOfWorkFactory(uow)
    clock = FakeClock(T0)
    agent = _agent(uow, key="archived-target")
    workflow, _, _ = _workflow(uow, [agent], [])
    task, run, generation = _root(uow, factory, workflow)
    # Archive the Workflow directly (bypassing ArchiveWorkflow's own use-case
    # checks) to simulate the Workflow being archived in the window between
    # binding and this Run's freeze/bootstrap.
    workflow.archive(T0)
    uow.workflows.save(workflow)

    with pytest.raises(WorkflowBindingError, match="archived"):
        StartWorkflowExecution(factory, clock, _runtime_registry()).execute(
            run.id, workflow.id, WORKER, TOKEN, generation
        )
    assert not uow.workflow_executions.items


def test_resolve_rejects_stale_claim() -> None:
    uow = FakeUnitOfWork()
    factory = CountingUnitOfWorkFactory(uow)
    clock = FakeClock(T0)
    agent = _agent(uow, key="claim-check")
    workflow, _, _ = _workflow(uow, [agent], [])
    task, run, generation = _root(uow, factory, workflow)

    with pytest.raises(ClaimLost):
        ResolveRunWorkflow(factory, clock).execute(
            run.id, workflow.id, WORKER, "wrong-token", generation
        )


def test_resolve_fails_closed_when_workflow_disappears_after_binding() -> None:
    uow = FakeUnitOfWork()
    factory = CountingUnitOfWorkFactory(uow)
    clock = FakeClock(T0)
    agent = _agent(uow, key="vanishing-target")
    workflow, _, _ = _workflow(uow, [agent], [])
    task, run, generation = _root(uow, factory, workflow)
    # Remove the Workflow entirely after the Task was already bound to it,
    # simulating durable state that disagrees with the binding.
    del uow.workflow_repo.items[workflow.id]

    with pytest.raises(WorkflowNotFound):
        ResolveRunWorkflow(factory, clock).execute(run.id, workflow.id, WORKER, TOKEN, generation)


def test_resolve_fails_closed_when_workflow_is_disabled() -> None:
    uow = FakeUnitOfWork()
    factory = CountingUnitOfWorkFactory(uow)
    clock = FakeClock(T0)
    agent = _agent(uow, key="disabled-target")
    workflow, _, _ = _workflow(uow, [agent], [])
    task, run, generation = _root(uow, factory, workflow)
    workflow.disable(T0)
    uow.workflows.save(workflow)

    with pytest.raises(WorkflowBindingError, match="active"):
        ResolveRunWorkflow(factory, clock).execute(run.id, workflow.id, WORKER, TOKEN, generation)
