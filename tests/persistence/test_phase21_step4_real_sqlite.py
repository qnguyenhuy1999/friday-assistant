"""Real SQLite closure proof for Phase 21 / Step 4.

Only the BrainRuntime is scripted. Migrated SQLite, UnitOfWork, work queue,
ClaimNextRun, Workflow bootstrap (StartWorkflowExecution), ResolveRunAgent,
AgentRunProcessor for every child node, approval lifecycle, ToolInvocation
lifecycle, and scheduler reconciliation (ReconcileWorkflowExecution) are all
production code -- exactly as PR #28 review item F12 requires.

Graph:

    A -> B
    A -> C
    B -> D
    C -> D
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from starlette.testclient import TestClient

from apps.api.app import create_app
from apps.api.settings import ApiSettings
from friday.application.agent_registry import (
    ActivateAgentRevision,
    CreateAgent,
    CreateAgentRevision,
    ResolveRunAgent,
)
from friday.application.agent_run_processor import AgentRunProcessor, RuntimeLimits
from friday.application.approval_workflow import ApproveRequest
from friday.application.brain_runtime import BrainRequest, BrainResponse
from friday.application.brain_runtime_registry import BrainRuntimeRegistry
from friday.application.claim_aware_tool_execution import ExecuteToolAction
from friday.application.commands import ApproveRequestCommand
from friday.application.ports import UnitOfWorkFactory
from friday.application.results import RunClaimResult
from friday.application.run_processor import ClaimContext
from friday.application.runtime_actions import FinishAction, InvokeToolAction
from friday.application.tool_authorization import RequestToolApproval
from friday.application.worker_coordination import (
    ApplySucceededOutcome,
    ClaimNextRun,
    VerifyRunClaim,
)
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
    AgentRevision,
    AgentRevisionSourceKind,
    Run,
    Task,
    TaskWorkflowBinding,
    WorkflowExecutionStatus,
    WorkflowNodeExecutionStatus,
    WorkflowRevisionSourceKind,
)
from friday.domain.identifiers import RunId, TaskId, WorkflowExecutionId
from friday.domain.run import RunStatus
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory
from friday.infrastructure.tools.gateway import WorkspaceToolGateway, WorkspaceToolGatewaySettings

REPO_ROOT = Path(__file__).resolve().parents[2]
AT = datetime(2026, 8, 12, 12, tzinfo=UTC)
LEASE = timedelta(minutes=5)


class FixedClock:
    def __init__(self) -> None:
        self._now = AT

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


class ScriptedBrain:
    """Deterministic BrainRuntime; all durable effects remain production-owned."""

    def __init__(self, *actions: object) -> None:
        self._actions = list(actions)
        self.requests: list[BrainRequest] = []

    def next_action(self, request: BrainRequest) -> BrainResponse:
        self.requests.append(request)
        if not self._actions:
            raise AssertionError("brain called beyond its script")
        action = self._actions.pop(0)
        assert isinstance(action, FinishAction | InvokeToolAction)
        return BrainResponse(action=action)


def _registry() -> BrainRuntimeRegistry:
    registry = BrainRuntimeRegistry()
    registry.register("claude_cli", lambda: None)  # type: ignore[arg-type,return-value]
    return registry


def _migrated_engine(tmp_path: Path, name: str) -> Engine:
    db_path = tmp_path / name
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(config, "head")
    return create_engine(f"sqlite:///{db_path}")


def _gateway(tmp_path: Path) -> WorkspaceToolGateway:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return WorkspaceToolGateway(
        WorkspaceToolGatewaySettings(
            workspace_root=workspace,
            max_file_bytes=64_000,
            max_list_entries=100,
            process_timeout_seconds=5,
            process_max_timeout_seconds=10,
            max_stdout_bytes=16_000,
            max_stderr_bytes=16_000,
        )
    )


def _active_agent(
    factory: UnitOfWorkFactory,
    clock: FixedClock,
    registry: BrainRuntimeRegistry,
    *,
    key: str,
    instructions: str,
) -> tuple[Agent, AgentRevision]:
    agent = CreateAgent(factory, clock).execute(key=key, display_name=key.title(), description="")
    revision = CreateAgentRevision(factory, clock, registry).execute(
        agent_id=agent.id,
        instructions=instructions,
        runtime_kind="claude_cli",
        runtime_config={},
        source_kind=AgentRevisionSourceKind.OPERATOR,
    )
    ActivateAgentRevision(factory, clock).execute(agent_id=agent.id, revision_id=revision.id)
    return agent, revision


def _processor(
    factory: UnitOfWorkFactory,
    clock: FixedClock,
    registry: BrainRuntimeRegistry,
    brain: ScriptedBrain,
    gateway: WorkspaceToolGateway,
) -> AgentRunProcessor:
    return AgentRunProcessor(
        uow_factory=factory,
        clock=clock,
        brain=brain,
        runtime_registry=registry,
        gateway=gateway,
        verify_claim=VerifyRunClaim(factory, clock),
        request_tool_approval=RequestToolApproval(factory, clock),
        execute_tool_action=ExecuteToolAction(factory, clock, gateway),
        limits=RuntimeLimits(
            max_turns_per_claim=8,
            max_tool_calls_per_claim=4,
            max_context_chars=60_000,
            max_response_bytes=65_536,
            max_yield_seconds=3600,
            max_processing_seconds=60,
        ),
    )


def _claim(factory: UnitOfWorkFactory, clock: FixedClock, worker_id: str) -> RunClaimResult:
    claim = ClaimNextRun(
        factory, clock, worker_id=worker_id, lease_duration=LEASE, candidate_limit=20
    ).execute()
    assert claim is not None
    return claim


def _context(claim: RunClaimResult) -> ClaimContext:
    return ClaimContext(
        run_id=claim.run_id,
        task_id=claim.task_id,
        worker_id=claim.worker_id,
        claim_token=claim.claim_token,
        claim_generation=claim.claim_generation,
        attempt_number=claim.attempt_number,
        is_lease_lost=lambda: False,
    )


def _bootstrap_graph(
    factory: UnitOfWorkFactory,
    clock: FixedClock,
    registry: BrainRuntimeRegistry,
    edges: list[tuple[str, str]],
    node_keys: list[str],
) -> tuple[RunId, dict[str, Agent], str]:
    agents: dict[str, Agent] = {}
    for key in node_keys:
        agent, _rev = _active_agent(
            factory, clock, registry, key=f"step4.agent.{key}", instructions=f"run node {key}"
        )
        agents[key] = agent

    workflow = CreateWorkflow(factory, clock).execute(
        key="step4.graph", display_name="Step 4 Graph", description=""
    )
    revision = CreateWorkflowRevision(factory, clock).execute(
        workflow_id=workflow.id,
        nodes=[
            {
                "node_key": key,
                "target_agent_id": str(agents[key].id),
                "objective": f"run node {key}",
                "input_payload": {"node": key},
                "expected_output_contract": "done",
            }
            for key in node_keys
        ],
        edges=[{"from_node": src, "to_node": dst} for src, dst in edges],
        source_kind=WorkflowRevisionSourceKind.OPERATOR,
    )
    ActivateWorkflowRevision(factory, clock).execute(
        workflow_id=workflow.id, revision_id=revision.id
    )

    task = Task.new(id=TaskId.new(), title="root", description="", created_at=AT)
    task.start(AT)
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=AT)
    run.start(AT)
    with factory() as uow:
        uow.tasks.add(task)
        uow.runs.add(run)
        uow.task_workflow_bindings.bind(
            TaskWorkflowBinding.new(task_id=task.id, workflow_id=workflow.id, at=AT)
        )
        uow.work_queue.enqueue(run.id, AT, AT)
        uow.commit()
    return run.id, agents, str(workflow.id)


def _run_next_child_node(
    factory: UnitOfWorkFactory,
    clock: FixedClock,
    registry: BrainRuntimeRegistry,
    gateway: WorkspaceToolGateway,
    execution_id: WorkflowExecutionId,
    summaries: dict[str, str],
) -> tuple[str, ScriptedBrain]:
    """Claim whichever dispatched child Run the queue hands back next --
    fan-out siblings (e.g. B and C) may be claimed in either order -- and
    run it to success through the real AgentRunProcessor. Returns the exact
    node_key that was claimed and the brain so the test can inspect the
    prompt context the child BrainRuntime received."""
    claim = _claim(factory, clock, "worker")
    with factory() as uow:
        node = uow.workflow_node_executions.get_by_child_execution_id(claim.run_id)
        assert node is not None
        node_key = node.node_key
    ResolveRunAgent(factory, clock, registry).execute(
        claim.run_id, claim.worker_id, claim.claim_token, claim.claim_generation
    )
    brain = ScriptedBrain(FinishAction(summary=summaries[node_key], details={"node": node_key}))
    outcome = _processor(factory, clock, registry, brain, gateway).process(_context(claim))
    assert outcome.kind == "succeeded"
    ApplySucceededOutcome(factory, clock).execute(
        claim.run_id,
        claim.worker_id,
        claim.claim_token,
        claim.claim_generation,
        outcome.final_response,
    )
    ReconcileWorkflowExecution(factory, clock).execute(execution_id)
    return node_key, brain


def test_real_step4_diamond_graph_end_to_end(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path, "step4-e2e.db")
    try:
        factory = create_unit_of_work_factory(create_session_factory(engine))
        clock = FixedClock()
        registry = _registry()
        gateway = _gateway(tmp_path)

        run_id, agents, workflow_id = _bootstrap_graph(
            factory,
            clock,
            registry,
            edges=[("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")],
            node_keys=["a", "b", "c", "d"],
        )

        root_claim = _claim(factory, clock, "root-worker")
        assert root_claim.run_id == run_id
        with factory() as uow:
            workflow_id_parsed = next(
                w.id for w in uow.workflows.list(10) if str(w.id) == workflow_id
            )
        execution = StartWorkflowExecution(factory, clock, registry).execute(
            root_claim.run_id,
            workflow_id_parsed,
            root_claim.worker_id,
            root_claim.claim_token,
            root_claim.claim_generation,
        )

        with factory() as uow:
            root_run = uow.runs.get(run_id)
            assert root_run is not None
            assert root_run.status is RunStatus.WAITING_FOR_WORKFLOW
            nodes = {
                n.node_key: n for n in uow.workflow_node_executions.list_by_execution(execution.id)
            }
            assert nodes["a"].status is WorkflowNodeExecutionStatus.DISPATCHED
            assert nodes["b"].status is WorkflowNodeExecutionStatus.PENDING
            assert nodes["c"].status is WorkflowNodeExecutionStatus.PENDING
            assert nodes["d"].status is WorkflowNodeExecutionStatus.PENDING

        summaries = {"a": "a result", "b": "b result", "c": "c result", "d": "d result"}

        node_a, brain_a = _run_next_child_node(
            factory, clock, registry, gateway, execution.id, summaries
        )
        assert node_a == "a"
        assert "# WORKFLOW NODE" in brain_a.requests[0].context
        assert "node_key: a" in brain_a.requests[0].context
        assert "# WORKFLOW PREDECESSORS" not in brain_a.requests[0].context

        with factory() as uow:
            nodes = {
                n.node_key: n for n in uow.workflow_node_executions.list_by_execution(execution.id)
            }
            assert nodes["a"].status is WorkflowNodeExecutionStatus.SUCCEEDED
            assert nodes["b"].status is WorkflowNodeExecutionStatus.DISPATCHED
            assert nodes["c"].status is WorkflowNodeExecutionStatus.DISPATCHED
            assert nodes["d"].status is WorkflowNodeExecutionStatus.PENDING

        node_first, brain_first = _run_next_child_node(
            factory, clock, registry, gateway, execution.id, summaries
        )
        assert node_first in {"b", "c"}
        assert f"node_key: {node_first}" in brain_first.requests[0].context

        with factory() as uow:
            nodes = {
                n.node_key: n for n in uow.workflow_node_executions.list_by_execution(execution.id)
            }
            # D remains PENDING after only one predecessor has succeeded.
            assert nodes["d"].status is WorkflowNodeExecutionStatus.PENDING

        node_second, brain_second = _run_next_child_node(
            factory, clock, registry, gateway, execution.id, summaries
        )
        assert {node_first, node_second} == {"b", "c"}
        assert f"node_key: {node_second}" in brain_second.requests[0].context

        with factory() as uow:
            nodes = {
                n.node_key: n for n in uow.workflow_node_executions.list_by_execution(execution.id)
            }
            assert nodes["d"].status is WorkflowNodeExecutionStatus.DISPATCHED

        node_d, brain_d = _run_next_child_node(
            factory, clock, registry, gateway, execution.id, summaries
        )
        assert node_d == "d"
        d_context = brain_d.requests[0].context
        assert "# WORKFLOW PREDECESSORS" in d_context
        assert "b result" in d_context
        assert "c result" in d_context
        assert "a result" not in d_context
        # Deterministic node-key ordering: "b" sorts before "c".
        assert d_context.index("- b:") < d_context.index("- c:")

        with factory() as uow:
            final_execution = uow.workflow_executions.get(execution.id)
            final_run = uow.runs.get(run_id)
            assert final_execution is not None and final_run is not None
            assert final_execution.status is WorkflowExecutionStatus.SUCCEEDED
            assert final_run.status is RunStatus.SUCCEEDED
            assert uow.approvals.list_for_run(run_id) == []
            assert uow.tool_invocations.list_for_run(run_id) == []

        settings = ApiSettings(
            database_url=f"sqlite:///{tmp_path / 'step4-e2e.db'}",
            host="127.0.0.1",
            port=8000,
            sse_poll_interval_seconds=0.001,
        )
        app = create_app(settings)
        try:
            client = TestClient(app)
            inspected = client.get(f"/v1/runs/{run_id}/workflow")
            assert inspected.status_code == 200
            assert inspected.json()["status"] == "succeeded"
            node_bodies = client.get(f"/v1/runs/{run_id}/workflow/nodes").json()
            assert [n["node_key"] for n in node_bodies] == ["a", "b", "c", "d"]
            assert all(n["status"] == "succeeded" for n in node_bodies)
        finally:
            app.state.engine.dispose()
    finally:
        engine.dispose()


def test_real_step4_two_nodes_have_independent_protected_tool_authority(
    tmp_path: Path,
) -> None:
    engine = _migrated_engine(tmp_path, "step4-authority.db")
    try:
        factory = create_unit_of_work_factory(create_session_factory(engine))
        clock = FixedClock()
        registry = _registry()
        gateway = _gateway(tmp_path)
        write = InvokeToolAction(
            tool="workspace.write_text",
            tool_input={"path": "authority-proof.txt", "content": "x", "overwrite": True},
            reason="protected proof",
        )

        run_id, agents, workflow_id = _bootstrap_graph(
            factory, clock, registry, edges=[], node_keys=["x", "y"]
        )
        del agents

        root_claim = _claim(factory, clock, "root-worker")
        with factory() as uow:
            workflow_id_parsed = next(
                w.id for w in uow.workflows.list(10) if str(w.id) == workflow_id
            )
        execution = StartWorkflowExecution(factory, clock, registry).execute(
            root_claim.run_id,
            workflow_id_parsed,
            root_claim.worker_id,
            root_claim.claim_token,
            root_claim.claim_generation,
        )

        node_x_claim = _claim(factory, clock, "worker-x")
        ResolveRunAgent(factory, clock, registry).execute(
            node_x_claim.run_id,
            node_x_claim.worker_id,
            node_x_claim.claim_token,
            node_x_claim.claim_generation,
        )
        brain_x = ScriptedBrain(write, write, FinishAction(summary="x done"))
        processor_x = _processor(factory, clock, registry, brain_x, gateway)
        waiting_x = processor_x.process(_context(node_x_claim))
        assert waiting_x.kind == "waiting_for_approval"
        assert waiting_x.approval_request_id is not None

        node_y_claim = _claim(factory, clock, "worker-y")
        ResolveRunAgent(factory, clock, registry).execute(
            node_y_claim.run_id,
            node_y_claim.worker_id,
            node_y_claim.claim_token,
            node_y_claim.claim_generation,
        )
        brain_y = ScriptedBrain(write, write, FinishAction(summary="y done"))
        processor_y = _processor(factory, clock, registry, brain_y, gateway)
        waiting_y = processor_y.process(_context(node_y_claim))
        assert waiting_y.kind == "waiting_for_approval"
        assert waiting_y.approval_request_id is not None

        with factory() as uow:
            approval_x = uow.approvals.get(waiting_x.approval_request_id)
            approval_y = uow.approvals.get(waiting_y.approval_request_id)
            assert approval_x is not None and approval_y is not None
            assert approval_x.id != approval_y.id
            assert approval_x.authorization_fingerprint != approval_y.authorization_fingerprint
            assert uow.tool_invocations.list_for_run(node_x_claim.run_id) == []
            assert uow.tool_invocations.list_for_run(node_y_claim.run_id) == []
            assert uow.approvals.list_for_run(run_id) == []
            assert uow.tool_invocations.list_for_run(run_id) == []

        ApproveRequest(factory, clock).execute(
            ApproveRequestCommand(waiting_x.approval_request_id, resolver="operator")
        )
        node_x_resumed = _claim(factory, clock, "worker-x-resumed")
        done_x = processor_x.process(_context(node_x_resumed))
        assert done_x.kind == "succeeded"
        ApplySucceededOutcome(factory, clock).execute(
            node_x_resumed.run_id,
            node_x_resumed.worker_id,
            node_x_resumed.claim_token,
            node_x_resumed.claim_generation,
            done_x.final_response,
        )

        with factory() as uow:
            x_invocations = uow.tool_invocations.list_for_run(node_x_claim.run_id)
            assert len(x_invocations) == 1
            assert x_invocations[0].approval_request_id == waiting_x.approval_request_id
            # y's approval must not have been consumable by x's action.
            y_invocations = uow.tool_invocations.list_for_run(node_y_claim.run_id)
            assert y_invocations == []
            approval_y_after = uow.approvals.get(waiting_y.approval_request_id)
            assert approval_y_after is not None and not approval_y_after.is_consumed

        ApproveRequest(factory, clock).execute(
            ApproveRequestCommand(waiting_y.approval_request_id, resolver="operator")
        )
        node_y_resumed = _claim(factory, clock, "worker-y-resumed")
        done_y = processor_y.process(_context(node_y_resumed))
        assert done_y.kind == "succeeded"
        ApplySucceededOutcome(factory, clock).execute(
            node_y_resumed.run_id,
            node_y_resumed.worker_id,
            node_y_resumed.claim_token,
            node_y_resumed.claim_generation,
            done_y.final_response,
        )

        with factory() as uow:
            y_invocations = uow.tool_invocations.list_for_run(node_y_claim.run_id)
            assert len(y_invocations) == 1
            assert y_invocations[0].approval_request_id == waiting_y.approval_request_id
            assert uow.approvals.list_for_run(run_id) == []
            assert uow.tool_invocations.list_for_run(run_id) == []

        ReconcileWorkflowExecution(factory, clock).execute(execution.id)
        with factory() as uow:
            final_execution = uow.workflow_executions.get(execution.id)
            assert final_execution is not None
            assert final_execution.status is WorkflowExecutionStatus.SUCCEEDED
            assert uow.approvals.list_for_run(run_id) == []
            assert uow.tool_invocations.list_for_run(run_id) == []
    finally:
        engine.dispose()
