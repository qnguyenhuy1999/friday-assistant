"""Real SQLite proof that WorkerLoop routes a Run by its durable frozen
RunWorkflowResolution first, never by the Task's current (mutable)
WorkflowBinding, once that Run has been frozen to a Workflow revision.

Only the ordinary Agent processor is faked (and asserted never invoked);
claiming, freezing, requeueing, unbinding, and Workflow bootstrap all use
the production use cases against a real migrated sqlite database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config

from apps.worker.worker_loop import WorkerLoop
from friday.application.agent_registry import (
    ActivateAgentRevision,
    CreateAgent,
    CreateAgentRevision,
)
from friday.application.brain_runtime_registry import BrainRuntimeRegistry
from friday.application.ports import UnitOfWorkFactory
from friday.application.retry_policy import RetryPolicy
from friday.application.run_processor import ClaimContext, ProcessingOutcome
from friday.application.worker_coordination import (
    ApplyFailedOutcome,
    ApplySucceededOutcome,
    ApplyWaitingOutcome,
    ClaimNextRun,
    RenewRunLease,
    RequeueClaimedRun,
)
from friday.application.worker_maintenance import ExpireDueApprovals, RecoverExpiredLeases
from friday.application.workflow_execution_use_cases import (
    ReconcileWorkflowExecution,
    ResolveRunWorkflow,
    StartWorkflowExecution,
    UnbindTaskWorkflow,
)
from friday.application.workflow_registry import (
    ActivateWorkflowRevision,
    CreateWorkflow,
    CreateWorkflowRevision,
)
from friday.domain import (
    AgentRevisionSourceKind,
    Run,
    RunStatus,
    Task,
    TaskWorkflowBinding,
    WorkflowExecutionStatus,
    WorkflowNodeExecutionStatus,
    WorkflowRevisionSourceKind,
)
from friday.domain.identifiers import RunId, TaskId
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory

REPO_ROOT = Path(__file__).resolve().parents[2]
AT = datetime(2026, 8, 12, 12, tzinfo=UTC)
LEASE = timedelta(minutes=5)


class _Clock:
    def now(self) -> datetime:
        return AT


@dataclass
class _CountingProcessor:
    """Proves ordinary Agent processing was never reached: WorkerLoop must
    resolve the frozen Workflow before it would ever construct a
    ClaimContext for the normal Agent path."""

    calls: list[ClaimContext] = field(default_factory=list)

    def process(self, context: ClaimContext) -> ProcessingOutcome:
        self.calls.append(context)
        raise AssertionError("ordinary Agent processing must not run for a Workflow-frozen Run")


def _factory(tmp_path: Path) -> UnitOfWorkFactory:
    database_url = f"sqlite:///{tmp_path / 'workflow-routing.db'}"
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return create_unit_of_work_factory(create_session_factory(create_engine(database_url)))


def _runtime_registry() -> BrainRuntimeRegistry:
    registry = BrainRuntimeRegistry()
    registry.register("claude_cli", lambda: None)  # type: ignore[arg-type,return-value]
    return registry


def _build_worker_loop(factory: UnitOfWorkFactory, processor_worker_id: str) -> WorkerLoop:
    clock = _Clock()
    return WorkerLoop(
        claim_next_run=ClaimNextRun(
            factory, clock, worker_id=processor_worker_id, lease_duration=LEASE, candidate_limit=10
        ),
        renew_lease=RenewRunLease(factory, clock, lease_duration=LEASE),
        requeue_claimed_run=RequeueClaimedRun(factory, clock),
        apply_failed=ApplyFailedOutcome(
            factory,
            clock,
            retry_policy=RetryPolicy(1, timedelta(seconds=1), 1, timedelta(seconds=1)),
        ),
        apply_succeeded=ApplySucceededOutcome(factory, clock),
        apply_waiting=ApplyWaitingOutcome(factory, clock),
        recover_expired_leases=RecoverExpiredLeases(factory, clock, batch_size=10),
        expire_due_approvals=ExpireDueApprovals(factory, clock, batch_size=10),
        clock=clock,
        heartbeat_interval_seconds=60.0,
        maintenance_interval_seconds=60.0,
        poll_interval_seconds=1.0,
        workflow_starter=StartWorkflowExecution(factory, clock, _runtime_registry()),
        workflow_reconciler=ReconcileWorkflowExecution(factory, clock),
        uow_factory=factory,
    )


def test_frozen_resolution_wins_over_unbound_task_workflow_binding(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    clock = _Clock()

    agent = CreateAgent(factory, clock).execute(
        key="routing.agent", display_name="Routing Agent", description=""
    )
    revision = CreateAgentRevision(factory, clock, _runtime_registry()).execute(
        agent_id=agent.id,
        instructions="run the node",
        runtime_kind="claude_cli",
        runtime_config={},
        source_kind=AgentRevisionSourceKind.OPERATOR,
    )
    ActivateAgentRevision(factory, clock).execute(agent_id=agent.id, revision_id=revision.id)

    workflow = CreateWorkflow(factory, clock).execute(
        key="routing.workflow", display_name="Routing Workflow", description=""
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
        uow.task_workflow_bindings.bind(
            TaskWorkflowBinding.new(task_id=task.id, workflow_id=workflow.id, at=AT)
        )
        uow.work_queue.enqueue(run.id, AT, AT)
        uow.commit()

    freezing_claim = ClaimNextRun(
        factory, clock, worker_id="worker-freeze", lease_duration=LEASE, candidate_limit=10
    ).execute()
    assert freezing_claim is not None and freezing_claim.run_id == run.id

    # Freeze the Run to Workflow revision A, then simulate a crash: the
    # claim is released back to the queue without ever bootstrapping the
    # WorkflowExecution, and the Task's WorkflowBinding is unbound entirely.
    resolution = ResolveRunWorkflow(factory, clock).execute(
        run.id,
        workflow.id,
        freezing_claim.worker_id,
        freezing_claim.claim_token,
        freezing_claim.claim_generation,
    )
    assert resolution.workflow_revision_id == workflow_revision.id

    RequeueClaimedRun(factory, clock).execute(
        run.id,
        freezing_claim.worker_id,
        freezing_claim.claim_token,
        freezing_claim.claim_generation,
        available_at=AT,
    )
    UnbindTaskWorkflow(factory).execute(task_id=task.id)
    with factory() as uow:
        assert uow.task_workflow_bindings.get_by_task_id(task.id) is None

    processor = _CountingProcessor()
    worker_loop = _build_worker_loop(factory, "worker-reclaim")

    handled = worker_loop.run_once(processor)

    assert handled is True
    assert processor.calls == []
    with factory() as uow:
        executions = uow.workflow_executions.list_by_root_run_id(run.id)
        assert len(executions) == 1
        assert executions[0].workflow_revision_id == workflow_revision.id
        assert executions[0].status is WorkflowExecutionStatus.RUNNING


def test_restarted_worker_recovers_workflow_after_child_terminal_commit(
    tmp_path: Path,
) -> None:
    """A crash after child terminalization cannot strand a Workflow root."""
    database_url = f"sqlite:///{tmp_path / 'workflow-routing.db'}"
    factory = _factory(tmp_path)
    clock = _Clock()
    registry = _runtime_registry()

    agent = CreateAgent(factory, clock).execute(
        key="recovery.agent", display_name="Recovery Agent", description=""
    )
    agent_revision = CreateAgentRevision(factory, clock, registry).execute(
        agent_id=agent.id,
        instructions="finish the workflow node",
        runtime_kind="claude_cli",
        runtime_config={},
        source_kind=AgentRevisionSourceKind.OPERATOR,
    )
    ActivateAgentRevision(factory, clock).execute(agent_id=agent.id, revision_id=agent_revision.id)
    workflow = CreateWorkflow(factory, clock).execute(
        key="recovery.workflow", display_name="Recovery Workflow", description=""
    )
    revision = CreateWorkflowRevision(factory, clock).execute(
        workflow_id=workflow.id,
        nodes=[
            {
                "node_key": "only",
                "target_agent_id": str(agent.id),
                "objective": "finish",
                "input_payload": {},
                "expected_output_contract": "done",
            }
        ],
        edges=[],
        source_kind=WorkflowRevisionSourceKind.OPERATOR,
    )
    ActivateWorkflowRevision(factory, clock).execute(
        workflow_id=workflow.id, revision_id=revision.id
    )

    root_task = Task.new(id=TaskId.new(), title="root", description="", created_at=AT)
    root_task.start(AT)
    root_run = Run.new(id=RunId.new(), task_id=root_task.id, created_at=AT)
    root_run.start(AT)
    with factory() as uow:
        uow.tasks.add(root_task)
        uow.runs.add(root_run)
        uow.task_workflow_bindings.bind(
            TaskWorkflowBinding.new(task_id=root_task.id, workflow_id=workflow.id, at=AT)
        )
        uow.work_queue.enqueue(root_run.id, AT, AT)
        uow.commit()

    original = _build_worker_loop(factory, "original-worker")
    processor = _CountingProcessor()
    assert original.run_once(processor) is True
    assert processor.calls == []
    with factory() as uow:
        execution = uow.workflow_executions.list_by_root_run_id(root_run.id)[0]
        node = uow.workflow_node_executions.list_by_execution(execution.id)[0]
        assert node.child_run_id is not None
        child_run_id = node.child_run_id
        assert execution.status is WorkflowExecutionStatus.RUNNING
        assert node.status is WorkflowNodeExecutionStatus.DISPATCHED
        uow.commit()

    child_claim = ClaimNextRun(
        factory, clock, worker_id="child-worker", lease_duration=LEASE, candidate_limit=10
    ).execute()
    assert child_claim is not None and child_claim.run_id == child_run_id
    ApplySucceededOutcome(factory, clock).execute(
        child_claim.run_id,
        child_claim.worker_id,
        child_claim.claim_token,
        child_claim.claim_generation,
        ("child finished", {"ordinary": "result"}),
    )

    # This is a new process boundary: it has an independent engine, session
    # factory, and WorkerLoop, and never receives the original callback.
    restarted_engine = create_engine(database_url)
    restarted_factory = create_unit_of_work_factory(create_session_factory(restarted_engine))
    try:
        restarted = _build_worker_loop(restarted_factory, "restarted-worker")
        restarted.run_maintenance_tick()
        with restarted_factory() as uow:
            execution = uow.workflow_executions.list_by_root_run_id(root_run.id)[0]
            node = uow.workflow_node_executions.list_by_execution(execution.id)[0]
            root = uow.runs.get(root_run.id)
            assert execution.status is WorkflowExecutionStatus.SUCCEEDED
            assert node.status is WorkflowNodeExecutionStatus.SUCCEEDED
            assert node.result_payload is not None
            assert isinstance(node.result_payload, dict)
            assert node.result_payload["summary"] == "child finished"
            assert node.result_payload["details"] == {"ordinary": "result"}
            assert root is not None and root.status is RunStatus.SUCCEEDED
            assert uow.work_queue.get(child_run_id) is None
            assert uow.work_queue.get(root_run.id) is None
            assert len(uow.runs.list_for_execution(child_run_id)) == 1
            uow.commit()
    finally:
        restarted_engine.dispose()
