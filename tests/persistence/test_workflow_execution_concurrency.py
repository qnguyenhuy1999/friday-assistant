"""Real SQLite, independent-session concurrency/idempotency proofs for the
Workflow scheduler, per PR #28 review item F11. No process-local locks are
used as correctness primitives anywhere here -- every guarantee comes from
the database (claim fencing, unique constraints) or from the reconciler's
own idempotent fixed-point logic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config

from friday.application.agent_registry import (
    ActivateAgentRevision,
    CreateAgent,
    CreateAgentRevision,
    ResolveRunAgent,
)
from friday.application.brain_runtime_registry import BrainRuntimeRegistry
from friday.application.ports import UnitOfWorkFactory
from friday.application.retry_policy import RetryPolicy
from friday.application.worker_coordination import ApplyFailedOutcome, ClaimNextRun
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
    AgentRevisionSourceKind,
    Run,
    Task,
    TaskWorkflowBinding,
    WorkflowExecutionStatus,
    WorkflowNodeExecutionStatus,
    WorkflowRevisionSourceKind,
)
from friday.domain.event import RunEventType
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import RunId, TaskId, WorkflowId
from friday.domain.run import RunStatus
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory

REPO_ROOT = Path(__file__).resolve().parents[2]
AT = datetime(2026, 8, 12, 12, tzinfo=UTC)
LEASE = timedelta(minutes=5)


class _Clock:
    def now(self) -> datetime:
        return AT


class _LaterClock:
    """A retry's created_at must be unambiguously later than its source's --
    get_latest_for_execution breaks created_at ties by (random) id -- and a
    retry's available_at is offset from its creation time by the retry
    policy's backoff delay."""

    def now(self) -> datetime:
        return AT + timedelta(minutes=1)


class _EvenLaterClock:
    """Used to claim a retry Run created via _LaterClock, whose
    available_at is further offset by the retry policy's backoff delay."""

    def now(self) -> datetime:
        return AT + timedelta(minutes=2)


def _factory(tmp_path: Path) -> UnitOfWorkFactory:
    database_url = f"sqlite:///{tmp_path / 'workflow-concurrency.db'}"
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return create_unit_of_work_factory(create_session_factory(create_engine(database_url)))


def _runtime_registry() -> BrainRuntimeRegistry:
    registry = BrainRuntimeRegistry()
    registry.register("claude_cli", lambda: None)  # type: ignore[arg-type,return-value]
    return registry


def _seed_graph(
    factory: UnitOfWorkFactory, edges: list[tuple[str, str]], node_keys: list[str]
) -> tuple[Task, Run, dict[str, str], str]:
    """Seed one Task/Run bound to a Workflow revision whose nodes each target
    their own dedicated Agent, wired by `edges` (node_key -> node_key)."""
    clock = _Clock()
    agent_ids: dict[str, str] = {}
    for key in node_keys:
        agent = CreateAgent(factory, clock).execute(
            key=f"concurrency.agent.{key}", display_name=key, description=""
        )
        agent_revision = CreateAgentRevision(factory, clock, _runtime_registry()).execute(
            agent_id=agent.id,
            instructions=f"run {key}",
            runtime_kind="claude_cli",
            runtime_config={},
            source_kind=AgentRevisionSourceKind.OPERATOR,
        )
        ActivateAgentRevision(factory, clock).execute(
            agent_id=agent.id, revision_id=agent_revision.id
        )
        agent_ids[key] = str(agent.id)

    workflow = CreateWorkflow(factory, clock).execute(
        key="concurrency.workflow", display_name="Concurrency Workflow", description=""
    )
    revision = CreateWorkflowRevision(factory, clock).execute(
        workflow_id=workflow.id,
        nodes=[
            {
                "node_key": key,
                "target_agent_id": agent_ids[key],
                "objective": f"run {key}",
                "input_payload": {},
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
    return task, run, agent_ids, str(workflow.id)


def _succeed_child(factory: UnitOfWorkFactory, child_run_id: RunId) -> None:
    with factory() as uow:
        child = uow.runs.get(child_run_id)
        assert child is not None
        child.start(AT)
        child.succeed(AT)
        uow.runs.save(child)
        uow.commit()


def test_stale_claim_cannot_bootstrap_a_second_workflow_execution(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    _, run, _, workflow_id = _seed_graph(factory, edges=[], node_keys=["root"])

    winning_claim = ClaimNextRun(
        factory, _Clock(), worker_id="worker-a", lease_duration=LEASE, candidate_limit=10
    ).execute()
    assert winning_claim is not None

    workflow_id_parsed = WorkflowId.parse(workflow_id)

    execution = StartWorkflowExecution(factory, _Clock(), _runtime_registry()).execute(
        run.id,
        workflow_id_parsed,
        winning_claim.worker_id,
        winning_claim.claim_token,
        winning_claim.claim_generation,
    )

    # A second, independent session racing after the root Run's claim has
    # already been consumed finds the existing execution (idempotent
    # bootstrap) instead of creating a second one -- it must never fabricate
    # a duplicate WorkflowExecution/RunWorkflowResolution/node set.
    raced = StartWorkflowExecution(factory, _Clock(), _runtime_registry()).execute(
        run.id,
        workflow_id_parsed,
        winning_claim.worker_id,
        winning_claim.claim_token,
        winning_claim.claim_generation,
    )
    assert raced.id == execution.id

    with factory() as uow:
        executions = uow.workflow_executions.list_by_root_run_id(run.id)
        assert len(executions) == 1
        assert executions[0].id == execution.id
        nodes = uow.workflow_node_executions.list_by_execution(execution.id)
        assert len(nodes) == 1
        assert nodes[0].status is WorkflowNodeExecutionStatus.DISPATCHED
        assert uow.run_workflow_resolutions.get_by_run_id(run.id) is not None


def test_fanin_join_dispatches_exactly_once_under_repeated_reconciliation(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    _, run, _, workflow_id = _seed_graph(
        factory,
        edges=[("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")],
        node_keys=["a", "b", "c", "d"],
    )
    claim = ClaimNextRun(
        factory, _Clock(), worker_id="worker-a", lease_duration=LEASE, candidate_limit=10
    ).execute()
    assert claim is not None
    workflow_id_parsed = WorkflowId.parse(workflow_id)
    execution = StartWorkflowExecution(factory, _Clock(), _runtime_registry()).execute(
        run.id, workflow_id_parsed, claim.worker_id, claim.claim_token, claim.claim_generation
    )

    def _node(node_key: str) -> object:
        with factory() as uow:
            nodes = uow.workflow_node_executions.list_by_execution(execution.id)
            return next(n for n in nodes if n.node_key == node_key)

    node_a = _node("a")
    _succeed_child(factory, node_a.child_run_id)  # type: ignore[attr-defined]
    ReconcileWorkflowExecution(factory, _Clock()).execute(execution.id)
    node_b, node_c = _node("b"), _node("c")
    _succeed_child(factory, node_b.child_run_id)  # type: ignore[attr-defined]
    _succeed_child(factory, node_c.child_run_id)  # type: ignore[attr-defined]

    # Two independent reconciliation calls race to dispatch the join node.
    ReconcileWorkflowExecution(factory, _Clock()).execute(execution.id)
    ReconcileWorkflowExecution(factory, _Clock()).execute(execution.id)

    with factory() as uow:
        node_d = next(
            n
            for n in uow.workflow_node_executions.list_by_execution(execution.id)
            if n.node_key == "d"
        )
        assert node_d.status is WorkflowNodeExecutionStatus.DISPATCHED
        assert node_d.child_task_id is not None
        assert node_d.child_execution_id is not None
        assert node_d.child_run_id is not None
        child_runs = uow.runs.list_for_execution(node_d.child_execution_id)
        assert len(child_runs) == 1
        queue_items = [item for item in [uow.work_queue.get(node_d.child_run_id)] if item]
        assert len(queue_items) == 1
        dispatch_events = [
            e
            for e in uow.events.list_for_run(run.id)
            if e.type is RunEventType.WORKFLOW_NODE_DISPATCHED
            and isinstance(e.payload, dict)
            and e.payload.get("node_key") == "d"
        ]
        assert len(dispatch_events) == 1


def test_automatic_retry_keeps_node_dispatched_and_inherits_frozen_agent_revision(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    _, run, agent_ids, workflow_id = _seed_graph(factory, edges=[("a", "b")], node_keys=["a", "b"])
    claim = ClaimNextRun(
        factory, _Clock(), worker_id="worker-a", lease_duration=LEASE, candidate_limit=10
    ).execute()
    assert claim is not None
    workflow_id_parsed = WorkflowId.parse(workflow_id)
    execution = StartWorkflowExecution(factory, _Clock(), _runtime_registry()).execute(
        run.id, workflow_id_parsed, claim.worker_id, claim.claim_token, claim.claim_generation
    )
    with factory() as uow:
        node_a = next(
            n
            for n in uow.workflow_node_executions.list_by_execution(execution.id)
            if n.node_key == "a"
        )
        child_run_id = node_a.child_run_id
        assert child_run_id is not None

    child_claim = ClaimNextRun(
        factory, _Clock(), worker_id="worker-child", lease_duration=LEASE, candidate_limit=10
    ).execute()
    assert child_claim is not None and child_claim.run_id == child_run_id

    # A real worker resolves the child Run's Agent identity (freezing the
    # exact revision) before ever failing/retrying it.
    resolution = ResolveRunAgent(factory, _Clock(), _runtime_registry()).execute(
        child_claim.run_id,
        child_claim.worker_id,
        child_claim.claim_token,
        child_claim.claim_generation,
    )
    assert resolution is not None
    assert str(resolution.agent_id) == agent_ids["a"]

    # get_latest_for_execution breaks created_at ties by id, which is a
    # random UUID -- advance the clock so the retry's created_at is
    # unambiguously later than the source's, exactly like the existing
    # delegation retry proof (test_phase21_step2_real_sqlite.py) already
    # does for the same reason.
    ApplyFailedOutcome(
        factory,
        _LaterClock(),
        retry_policy=RetryPolicy(2, timedelta(seconds=1), 1, timedelta(seconds=1)),
    ).execute(
        child_claim.run_id,
        child_claim.worker_id,
        child_claim.claim_token,
        child_claim.claim_generation,
        Failure("worker_timeout", "worker timed out", True, FailureCause.TIMEOUT),
    )

    # Reconciling while the automatic retry is pending must not resurrect the
    # node past DISPATCHED, and node-b must not dispatch early.
    nodes = ReconcileWorkflowExecution(factory, _Clock()).execute(execution.id)
    node_a_after = next(n for n in nodes if n.node_key == "a")
    node_b_after = next(n for n in nodes if n.node_key == "b")
    assert node_a_after.status is WorkflowNodeExecutionStatus.DISPATCHED
    assert node_b_after.status is WorkflowNodeExecutionStatus.PENDING

    with factory() as uow:
        retry_runs = [r for r in uow.runs.list_for_execution(child_run_id) if r.id != child_run_id]
        assert len(retry_runs) == 1
        retry = retry_runs[0]
        retry_resolution = uow.run_agent_resolutions.get(retry.id)
        assert retry_resolution is not None
        assert str(retry_resolution.agent_id) == agent_ids["a"]

    retry_claim = ClaimNextRun(
        factory,
        _EvenLaterClock(),
        worker_id="worker-retry",
        lease_duration=LEASE,
        candidate_limit=10,
    ).execute()
    assert retry_claim is not None
    with factory() as uow:
        retried_run = uow.runs.get(retry_claim.run_id)
        assert retried_run is not None
        retried_run.succeed(_EvenLaterClock().now())
        uow.runs.save(retried_run)
        uow.commit()

    final_nodes = ReconcileWorkflowExecution(factory, _Clock()).execute(execution.id)
    node_a_final = next(n for n in final_nodes if n.node_key == "a")
    node_b_final = next(n for n in final_nodes if n.node_key == "b")
    assert node_a_final.status is WorkflowNodeExecutionStatus.SUCCEEDED
    assert node_b_final.status is WorkflowNodeExecutionStatus.DISPATCHED


def test_terminal_workflow_rejects_stale_reconciliation_and_dispatches_nothing_further(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    _, run, _, workflow_id = _seed_graph(factory, edges=[], node_keys=["root"])
    claim = ClaimNextRun(
        factory, _Clock(), worker_id="worker-a", lease_duration=LEASE, candidate_limit=10
    ).execute()
    assert claim is not None
    workflow_id_parsed = WorkflowId.parse(workflow_id)
    execution = StartWorkflowExecution(factory, _Clock(), _runtime_registry()).execute(
        run.id, workflow_id_parsed, claim.worker_id, claim.claim_token, claim.claim_generation
    )
    with factory() as uow:
        node = uow.workflow_node_executions.list_by_execution(execution.id)[0]
        child_run_id = node.child_run_id
    _succeed_child(factory, child_run_id)  # type: ignore[arg-type]
    ReconcileWorkflowExecution(factory, _Clock()).execute(execution.id)

    with factory() as uow:
        completed_execution = uow.workflow_executions.get(execution.id)
        assert completed_execution is not None
        assert completed_execution.status is WorkflowExecutionStatus.SUCCEEDED
        completed_run = uow.runs.get(run.id)
        assert completed_run is not None
        assert completed_run.status is RunStatus.SUCCEEDED
        before_events = list(uow.events.list_for_run(run.id))

    # A stale reconciliation call after the Workflow already terminalized
    # must be a durable no-op: no re-dispatch, no resurrected root, no new
    # events.
    ReconcileWorkflowExecution(factory, _Clock()).execute(execution.id)

    with factory() as uow:
        after_execution = uow.workflow_executions.get(execution.id)
        assert after_execution is not None
        assert after_execution.status is WorkflowExecutionStatus.SUCCEEDED
        after_run = uow.runs.get(run.id)
        assert after_run is not None
        assert after_run.status is RunStatus.SUCCEEDED
        after_events = list(uow.events.list_for_run(run.id))
        assert len(after_events) == len(before_events)


def test_workflow_execution_status_update_race_is_idempotent_or_conflict_free(
    tmp_path: Path,
) -> None:
    from friday.application.errors import ConcurrencyConflict
    from friday.infrastructure.persistence.repositories import WorkflowExecutionRepository

    factory = _factory(tmp_path)
    _, run, _, workflow_id = _seed_graph(factory, edges=[], node_keys=["root"])
    claim = ClaimNextRun(
        factory, _Clock(), worker_id="worker-a", lease_duration=LEASE, candidate_limit=10
    ).execute()
    assert claim is not None
    workflow_id_parsed = WorkflowId.parse(workflow_id)
    execution = StartWorkflowExecution(factory, _Clock(), _runtime_registry()).execute(
        run.id, workflow_id_parsed, claim.worker_id, claim.claim_token, claim.claim_generation
    )

    database_url = f"sqlite:///{tmp_path / 'workflow-concurrency.db'}"
    session_factory = create_session_factory(create_engine(database_url))
    session_a, session_b, session_c = session_factory(), session_factory(), session_factory()
    try:
        WorkflowExecutionRepository(session_a).update_status(
            execution.id, WorkflowExecutionStatus.SUCCEEDED, completed_at=AT
        )
        session_a.commit()

        # A second, independent session racing to apply the exact same
        # already-applied transition observes it already happened and
        # returns without raising (idempotent, not a lost race).
        WorkflowExecutionRepository(session_b).update_status(
            execution.id, WorkflowExecutionStatus.SUCCEEDED, completed_at=AT
        )
        session_b.commit()

        # A third, independent session racing to apply a *different*
        # transition than the one that already won must fail closed rather
        # than silently overwrite the durable outcome.
        try:
            WorkflowExecutionRepository(session_c).update_status(
                execution.id,
                WorkflowExecutionStatus.FAILED,
                completed_at=AT,
                failure_code="late",
                failure_message="lost the race",
            )
            raised = False
        except ConcurrencyConflict:
            raised = True
        session_c.rollback()
        assert raised
    finally:
        session_a.close()
        session_b.close()
        session_c.close()

    with factory() as uow:
        final = uow.workflow_executions.get(execution.id)
        assert final is not None
        assert final.status is WorkflowExecutionStatus.SUCCEEDED


def test_workflow_node_execution_status_update_race_is_idempotent_or_conflict_free(
    tmp_path: Path,
) -> None:
    from friday.application.errors import ConcurrencyConflict
    from friday.infrastructure.persistence.repositories import WorkflowNodeExecutionRepository

    factory = _factory(tmp_path)
    _, run, _, workflow_id = _seed_graph(factory, edges=[], node_keys=["root"])
    claim = ClaimNextRun(
        factory, _Clock(), worker_id="worker-a", lease_duration=LEASE, candidate_limit=10
    ).execute()
    assert claim is not None
    workflow_id_parsed = WorkflowId.parse(workflow_id)
    execution = StartWorkflowExecution(factory, _Clock(), _runtime_registry()).execute(
        run.id, workflow_id_parsed, claim.worker_id, claim.claim_token, claim.claim_generation
    )
    with factory() as uow:
        node = uow.workflow_node_executions.list_by_execution(execution.id)[0]
        node_id = node.id
        child_task_id, child_run_id, child_execution_id = (
            node.child_task_id,
            node.child_run_id,
            node.child_execution_id,
        )

    database_url = f"sqlite:///{tmp_path / 'workflow-concurrency.db'}"
    session_factory = create_session_factory(create_engine(database_url))
    session_a, session_b, session_c = session_factory(), session_factory(), session_factory()
    try:
        common_kwargs = {
            "child_task_id": child_task_id,
            "child_run_id": child_run_id,
            "child_execution_id": child_execution_id,
            "started_at": AT,
        }
        WorkflowNodeExecutionRepository(session_a).update_status(
            node_id,
            WorkflowNodeExecutionStatus.SUCCEEDED,
            completed_at=AT,
            **common_kwargs,  # type: ignore[arg-type]
        )
        session_a.commit()

        # Idempotent: a second session racing to apply the same transition
        # observes it already happened.
        WorkflowNodeExecutionRepository(session_b).update_status(
            node_id,
            WorkflowNodeExecutionStatus.SUCCEEDED,
            completed_at=AT,
            **common_kwargs,  # type: ignore[arg-type]
        )
        session_b.commit()

        try:
            WorkflowNodeExecutionRepository(session_c).update_status(
                node_id,
                WorkflowNodeExecutionStatus.FAILED,
                completed_at=AT,
                failure_code="late",
                failure_message="lost the race",
                **common_kwargs,  # type: ignore[arg-type]
            )
            raised = False
        except ConcurrencyConflict:
            raised = True
        session_c.rollback()
        assert raised
    finally:
        session_a.close()
        session_b.close()
        session_c.close()

    with factory() as uow:
        final = uow.workflow_node_executions.get(node_id)
        assert final is not None
        assert final.status is WorkflowNodeExecutionStatus.SUCCEEDED
        assert uow.work_queue.get(run.id) is None
