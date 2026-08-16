"""Real SQLite closure proofs for Phase 21 / Step 5A — bounded nested
delegation.

Two levels of proof live here.

The primary E2E (``test_worker_loop_real_sqlite_nested_chain_...`` and the
worker-driven depth-exhaustion proof) drives every hop through the production
WorkerLoop with one shared AgentRunProcessor: ClaimNextRun, claim fencing,
DispatchDelegation, ResolveRunAgent / RunAgentResolution, normal outcome
application, and delegation reconciliation all run through the same
orchestration a deployed worker uses. Only the BrainRuntime is scripted.

The remaining tests are focused integration proofs that compose the same
production components directly (migrated SQLite, the real UnitOfWork, work
queue, AgentRunProcessor, and the outcome appliers) without the loop.

Chain under test:

    A
    └─ delegation AB -> B (depth 1, root AB)
       └─ delegation BC -> C (depth 2, root AB)

Immediate-parent reconciliation must resume B when C terminalizes and A only
when B terminalizes; authority never crosses a hop.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, func, select, text

from apps.worker.worker_loop import WorkerLoop
from friday.application.agent_registry import (
    ActivateAgentRevision,
    CreateAgent,
    CreateAgentRevision,
)
from friday.application.agent_run_processor import AgentRunProcessor, RuntimeLimits
from friday.application.brain_runtime import BrainRequest, BrainResponse
from friday.application.brain_runtime_registry import BrainRuntimeRegistry
from friday.application.claim_aware_tool_execution import ExecuteToolAction
from friday.application.commands import CreateTaskCommand, StartRunCommand
from friday.application.create_task import CreateTask
from friday.application.delegation import DispatchDelegation
from friday.application.delegation_reconciliation import reconcile_child_terminal_in_uow
from friday.application.errors import ClaimLost
from friday.application.ports import UnitOfWorkFactory
from friday.application.results import RunClaimResult
from friday.application.retry_policy import RetryPolicy
from friday.application.run_processor import ClaimContext, ProcessingOutcome
from friday.application.runtime_actions import DelegateAction, FinishAction, YieldAction
from friday.application.start_run import StartRun
from friday.application.tool_authorization import RequestToolApproval
from friday.application.worker_coordination import (
    ApplyFailedOutcome,
    ApplySucceededOutcome,
    ApplyWaitingForDelegationOutcome,
    ApplyWaitingOutcome,
    ClaimNextRun,
    RenewRunLease,
    RequeueClaimedRun,
    VerifyRunClaim,
)
from friday.application.worker_maintenance import (
    ExpireDueApprovals,
    MaterializeScheduledAnswerDeliveries,
    RecoverExpiredLeases,
)
from friday.application.workflow_execution_use_cases import (
    ReconcileWorkflowExecution,
    StartWorkflowExecution,
)
from friday.domain.agent import Agent, AgentRevision, AgentRevisionSourceKind
from friday.domain.delegation import MAX_DELEGATION_DEPTH
from friday.domain.identifiers import DelegationRequestId, RunId
from friday.domain.run import RunStatus
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.models import DelegationRequestRow, RunRow, TaskRow
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory
from friday.infrastructure.tools.gateway import WorkspaceToolGateway, WorkspaceToolGatewaySettings

REPO_ROOT = Path(__file__).resolve().parents[2]
AT = datetime(2026, 8, 16, 12, tzinfo=UTC)
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
        assert isinstance(action, (DelegateAction, FinishAction))
        return BrainResponse(action=action)


class WorkerScriptedBrain:
    """Per-run deterministic BrainRuntime for the production worker path.

    Child Run IDs only exist once DispatchDelegation materializes them, so the
    test scripts each Run's actions as they become known between worker
    iterations; an unscripted or over-called Run fails loudly. Every durable
    effect stays with the WorkerLoop, AgentRunProcessor, and appliers."""

    def __init__(self) -> None:
        self._scripts: dict[RunId, list[object]] = {}
        self.requests: list[BrainRequest] = []

    def script(self, run_id: RunId, *actions: object) -> None:
        self._scripts[run_id] = list(actions)

    def next_action(self, request: BrainRequest) -> BrainResponse:
        self.requests.append(request)
        actions = self._scripts.get(request.run_id)
        if not actions:
            raise AssertionError(f"brain called beyond its script for run {request.run_id}")
        action = actions.pop(0)
        assert isinstance(action, (DelegateAction, FinishAction, YieldAction))
        return BrainResponse(action=action)

    def requests_for(self, run_id: RunId) -> list[BrainRequest]:
        return [request for request in self.requests if request.run_id == run_id]


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
    agent = CreateAgent(factory, clock).execute(
        key=key, display_name=key.title(), description="step-5 chain hop"
    )
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
            max_delegation_depth=MAX_DELEGATION_DEPTH,
        ),
    )


def _claim(factory: UnitOfWorkFactory, clock: FixedClock, worker_id: str) -> RunClaimResult:
    claim = ClaimNextRun(
        factory,
        clock,
        worker_id=worker_id,
        lease_duration=LEASE,
        candidate_limit=20,
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


def _worker_harness(
    factory: UnitOfWorkFactory,
    clock: FixedClock,
    registry: BrainRuntimeRegistry,
    brain: WorkerScriptedBrain,
    gateway: WorkspaceToolGateway,
) -> tuple[WorkerLoop, AgentRunProcessor]:
    """The production apps.worker.app composition with only the BrainRuntime
    scripted: one shared AgentRunProcessor and one WorkerLoop drive every
    claim, outcome application, and delegation reconciliation in the chain."""
    processor = AgentRunProcessor(
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
            max_delegation_depth=MAX_DELEGATION_DEPTH,
        ),
    )
    loop = WorkerLoop(
        claim_next_run=ClaimNextRun(
            factory,
            clock,
            worker_id="step5-worker-loop",
            lease_duration=LEASE,
            candidate_limit=20,
        ),
        renew_lease=RenewRunLease(factory, clock, lease_duration=LEASE),
        requeue_claimed_run=RequeueClaimedRun(factory, clock),
        apply_failed=ApplyFailedOutcome(
            factory,
            clock,
            retry_policy=RetryPolicy(3, timedelta(seconds=1), 2.0, timedelta(seconds=10)),
        ),
        apply_succeeded=ApplySucceededOutcome(factory, clock),
        apply_waiting=ApplyWaitingOutcome(factory, clock),
        apply_waiting_for_delegation=ApplyWaitingForDelegationOutcome(factory, clock),
        recover_expired_leases=RecoverExpiredLeases(factory, clock, batch_size=20),
        expire_due_approvals=ExpireDueApprovals(factory, clock, batch_size=20),
        materialize_scheduled_answers=MaterializeScheduledAnswerDeliveries(
            factory, clock, batch_size=20
        ),
        clock=clock,
        heartbeat_interval_seconds=3600.0,
        maintenance_interval_seconds=3600.0,
        poll_interval_seconds=0.001,
        workflow_starter=StartWorkflowExecution(factory, clock, registry),
        workflow_reconciler=ReconcileWorkflowExecution(factory, clock),
        uow_factory=factory,
    )
    return loop, processor


def _dispatched_child(
    factory: UnitOfWorkFactory, parent_run_id: RunId
) -> tuple[DelegationRequestId, RunId]:
    """Read back exactly one dispatched hop's request and child Run."""
    with factory() as uow:
        requests = uow.delegation_requests.list_for_run(parent_run_id)
        assert len(requests) == 1
        assert requests[0].child_run_id is not None
        pair = (requests[0].id, requests[0].child_run_id)
        uow.commit()
    return pair


def _dispatch_via_processor(
    factory: UnitOfWorkFactory,
    clock: FixedClock,
    registry: BrainRuntimeRegistry,
    gateway: WorkspaceToolGateway,
    claim: RunClaimResult,
    target_key: str,
    objective: str,
) -> DelegationRequestId:
    """Drive one normal DelegateAction through the real processor and applier."""
    brain = ScriptedBrain(
        DelegateAction(
            target_agent_key=target_key,
            objective=objective,
            input_payload={"hop": objective},
            expected_output_contract="Return the bounded result.",
            reason="nested chain hop",
        )
    )
    outcome = _processor(factory, clock, registry, brain, gateway).process(_context(claim))
    assert outcome.kind == "waiting_for_delegation"
    assert outcome.delegation_request_id is not None
    ApplyWaitingForDelegationOutcome(factory, clock).execute(
        claim.run_id,
        claim.worker_id,
        claim.claim_token,
        claim.claim_generation,
        outcome.delegation_request_id,
    )
    return outcome.delegation_request_id


def _finish_via_processor(
    factory: UnitOfWorkFactory,
    clock: FixedClock,
    registry: BrainRuntimeRegistry,
    gateway: WorkspaceToolGateway,
    claim: RunClaimResult,
    summary: str,
) -> tuple[ProcessingOutcome, ScriptedBrain]:
    brain = ScriptedBrain(FinishAction(summary=summary, details={"from": summary}))
    outcome = _processor(factory, clock, registry, brain, gateway).process(_context(claim))
    assert outcome.kind == "succeeded"
    ApplySucceededOutcome(factory, clock).execute(
        claim.run_id,
        claim.worker_id,
        claim.claim_token,
        claim.claim_generation,
        outcome.final_response,
    )
    return outcome, brain


def _counts(engine: Engine) -> tuple[int, int, int]:
    """Global durable row counts: (tasks, runs, delegation_requests)."""
    with engine.connect() as connection:
        tasks = connection.scalar(select(func.count()).select_from(TaskRow))
        runs = connection.scalar(select(func.count()).select_from(RunRow))
        delegations = connection.scalar(select(func.count()).select_from(DelegationRequestRow))
    assert tasks is not None and runs is not None and delegations is not None
    return int(tasks), int(runs), int(delegations)


def _event_types(factory: UnitOfWorkFactory, run_id: RunId) -> list[str]:
    with factory() as uow:
        types = [event.type.value for event in uow.events.list_for_run(run_id)]
        uow.commit()
    return types


def test_real_sqlite_nested_chain_a_to_b_to_c_closes_through_immediate_parents(
    tmp_path: Path,
) -> None:
    engine = _migrated_engine(tmp_path, "phase21-step5-chain.db")
    try:
        factory = create_unit_of_work_factory(create_session_factory(engine))
        clock = FixedClock()
        registry = _registry()
        gateway = _gateway(tmp_path)
        root_task = CreateTask(factory, clock).execute(
            CreateTaskCommand("A orchestration", "step-5 nested chain")
        )
        started = StartRun(factory, clock).execute(StartRunCommand(root_task.task_id))
        assert started.run_id is not None
        run_a = started.run_id
        agent_b, revision_b = _active_agent(
            factory,
            clock,
            registry,
            key="step5.b",
            instructions="Mid-chain agent: consume the child result, then finish.",
        )
        agent_c, revision_c = _active_agent(
            factory,
            clock,
            registry,
            key="step5.c",
            instructions="Leaf agent: finish with the bounded result.",
        )

        # A -> B (depth 1, root AB)
        a_claim = _claim(factory, clock, "a-worker")
        assert a_claim.run_id == run_a
        ab_id = _dispatch_via_processor(
            factory, clock, registry, gateway, a_claim, agent_b.key, "A delegates to B"
        )

        # B -> C (depth 2, root AB): a delegated child Run issues a normal
        # DelegateAction through the ordinary DispatchDelegation path.
        b_claim = _claim(factory, clock, "b-worker")
        bc_id = _dispatch_via_processor(
            factory, clock, registry, gateway, b_claim, agent_c.key, "B delegates to C"
        )

        # C executes normally through ResolveRunAgent -> AgentRunProcessor.
        c_claim = _claim(factory, clock, "c-worker")
        c_outcome, c_brain = _finish_via_processor(
            factory, clock, registry, gateway, c_claim, "c evidence complete"
        )
        assert c_outcome.kind == "succeeded"
        assert c_brain.requests[0].run_id == c_claim.run_id
        assert "# DELEGATED WORK" in c_brain.requests[0].context

        # B resumes and consumes C's bounded result through normal context.
        b_resume_claim = _claim(factory, clock, "b-worker-resumed")
        assert b_resume_claim.run_id == b_claim.run_id
        b_outcome, b_brain = _finish_via_processor(
            factory, clock, registry, gateway, b_resume_claim, "b consumed c result"
        )
        assert b_outcome.kind == "succeeded"
        assert "# DELEGATIONS" in b_brain.requests[-1].context
        assert "summary=c evidence complete" in b_brain.requests[-1].context

        # A resumes and consumes B's bounded result through normal context.
        a_resume_claim = _claim(factory, clock, "a-worker-resumed")
        assert a_resume_claim.run_id == run_a
        a_outcome, a_brain = _finish_via_processor(
            factory, clock, registry, gateway, a_resume_claim, "a consumed b result"
        )
        assert a_outcome.kind == "succeeded"
        assert "# DELEGATIONS" in a_brain.requests[-1].context
        assert "summary=b consumed c result" in a_brain.requests[-1].context

        with factory() as uow:
            ab = uow.delegation_requests.get(ab_id)
            bc = uow.delegation_requests.get(bc_id)
            assert ab is not None and bc is not None
            assert ab.parent_run_id == run_a
            assert bc.parent_run_id == b_claim.run_id
            assert ab.depth == 1 and ab.root_delegation_id == ab.id
            assert bc.depth == 2 and bc.root_delegation_id == ab.id
            assert ab.status.value == "succeeded" and bc.status.value == "succeeded"
            assert ab.child_run_id == b_claim.run_id and bc.child_run_id == c_claim.run_id
            # Exact Agent freeze exists for each delegated child.
            b_resolution = uow.run_agent_resolutions.get(b_claim.run_id)
            c_resolution = uow.run_agent_resolutions.get(c_claim.run_id)
            assert b_resolution is not None
            assert b_resolution.agent_id == agent_b.id
            assert b_resolution.revision_id == revision_b.id
            assert c_resolution is not None
            assert c_resolution.agent_id == agent_c.id
            assert c_resolution.revision_id == revision_c.id
            # No duplicated queue materialization survives the chain.
            assert uow.work_queue.get(run_a) is None
            assert uow.work_queue.get(b_claim.run_id) is None
            assert uow.work_queue.get(c_claim.run_id) is None
            uow.commit()

        # Exactly one request per hop, one child Task/Run per dispatch.
        tasks, runs, delegations = _counts(engine)
        assert (tasks, runs, delegations) == (3, 3, 2)

        # No duplicated lifecycle transitions.
        a_events = _event_types(factory, run_a)
        b_events = _event_types(factory, b_claim.run_id)
        c_events = _event_types(factory, c_claim.run_id)
        assert a_events.count("delegation_dispatched") == 1
        assert a_events.count("run_waiting_for_delegation") == 1
        assert a_events.count("delegation_succeeded") == 1
        assert a_events.count("run_resumed") == 1
        assert a_events.count("run_started") == 1
        assert b_events.count("delegation_dispatched") == 1
        assert b_events.count("run_waiting_for_delegation") == 1
        assert b_events.count("delegation_succeeded") == 1
        assert b_events.count("run_resumed") == 1
        assert b_events.count("run_started") == 1
        assert c_events.count("run_started") == 1
        assert c_events.count("delegation_dispatched") == 0
        assert c_events.count("run_resumed") == 0
    finally:
        engine.dispose()


def test_real_sqlite_wait_ownership_while_c_is_active_and_after_c_terminalizes(
    tmp_path: Path,
) -> None:
    engine = _migrated_engine(tmp_path, "phase21-step5-wait-ownership.db")
    try:
        factory = create_unit_of_work_factory(create_session_factory(engine))
        clock = FixedClock()
        registry = _registry()
        gateway = _gateway(tmp_path)
        root_task = CreateTask(factory, clock).execute(
            CreateTaskCommand("A wait ownership", "step-5 wait ownership")
        )
        started = StartRun(factory, clock).execute(StartRunCommand(root_task.task_id))
        assert started.run_id is not None
        run_a = started.run_id
        agent_b, _revision_b = _active_agent(
            factory, clock, registry, key="step5.wait.b", instructions="mid hop"
        )
        agent_c, _revision_c = _active_agent(
            factory, clock, registry, key="step5.wait.c", instructions="leaf hop"
        )

        ab_id = _dispatch_via_processor(
            factory,
            clock,
            registry,
            gateway,
            _claim(factory, clock, "a-worker"),
            agent_b.key,
            "A delegates to B",
        )
        b_claim = _claim(factory, clock, "b-worker")
        bc_id = _dispatch_via_processor(
            factory, clock, registry, gateway, b_claim, agent_c.key, "B delegates to C"
        )
        c_claim = _claim(factory, clock, "c-worker")

        # Required state while C is active: each Run waits only on its own
        # immediate DelegationRequest.
        with factory() as uow:
            row_a = uow.runs.get(run_a)
            row_b = uow.runs.get(b_claim.run_id)
            row_c = uow.runs.get(c_claim.run_id)
            assert row_a is not None and row_a.status is RunStatus.WAITING_FOR_DELEGATION
            assert row_a.delegation_request_id == ab_id
            assert row_b is not None and row_b.status is RunStatus.WAITING_FOR_DELEGATION
            assert row_b.delegation_request_id == bc_id
            assert row_c is not None and row_c.status is RunStatus.RUNNING
            uow.commit()

        _finish_via_processor(factory, clock, registry, gateway, c_claim, "c done")

        with factory() as uow:
            bc = uow.delegation_requests.get(bc_id)
            row_a = uow.runs.get(run_a)
            row_b = uow.runs.get(b_claim.run_id)
            assert bc is not None and bc.status.value == "succeeded"
            assert row_b is not None and row_b.status is RunStatus.RUNNING
            assert row_b.delegation_request_id is None
            assert uow.work_queue.get(b_claim.run_id) is not None
            # A is untouched until B terminalizes.
            assert row_a is not None and row_a.status is RunStatus.WAITING_FOR_DELEGATION
            assert row_a.delegation_request_id == ab_id
            assert uow.work_queue.get(run_a) is None
            uow.commit()
    finally:
        engine.dispose()


def test_real_sqlite_stale_nested_claim_creates_zero_child_state(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path, "phase21-step5-stale-claim.db")
    try:
        factory = create_unit_of_work_factory(create_session_factory(engine))
        clock = FixedClock()
        registry = _registry()
        gateway = _gateway(tmp_path)
        root_task = CreateTask(factory, clock).execute(
            CreateTaskCommand("A stale claim", "step-5 stale claim")
        )
        started = StartRun(factory, clock).execute(StartRunCommand(root_task.task_id))
        assert started.run_id is not None
        agent_b, _revision_b = _active_agent(
            factory, clock, registry, key="step5.stale.b", instructions="mid hop"
        )
        agent_c, _revision_c = _active_agent(
            factory, clock, registry, key="step5.stale.c", instructions="never reached"
        )
        _dispatch_via_processor(
            factory,
            clock,
            registry,
            gateway,
            _claim(factory, clock, "a-worker"),
            agent_b.key,
            "A delegates to B",
        )
        b_claim = _claim(factory, clock, "b-worker-1")
        before = _counts(engine)
        with factory() as uow:
            before_events = _event_types(factory, b_claim.run_id)
            uow.commit()

        # Fence B's first worker out of durable state: its lease expires and a
        # second worker steals the claim, bumping claim_generation.
        clock.advance(LEASE + timedelta(seconds=1))
        stolen = _claim(factory, clock, "b-worker-2")
        assert stolen.run_id == b_claim.run_id

        with pytest.raises(ClaimLost):
            DispatchDelegation(factory, clock, registry).execute(
                parent_run_id=b_claim.run_id,
                worker_id=b_claim.worker_id,
                claim_token=b_claim.claim_token,
                claim_generation=b_claim.claim_generation,
                target_agent_key=agent_c.key,
                objective="B delegates to C with a stale claim",
                input_payload={},
                expected_output_contract="result",
            )

        assert _counts(engine) == before
        with factory() as uow:
            row_b = uow.runs.get(b_claim.run_id)
            assert row_b is not None and row_b.status is RunStatus.RUNNING
            assert row_b.delegation_request_id is None
            assert uow.work_queue.get(b_claim.run_id) is not None
            b_item = uow.work_queue.get(b_claim.run_id)
            assert b_item is not None and b_item.claimed_by == "b-worker-2"
            outgoing = uow.delegation_requests.list_for_run(b_claim.run_id)
            assert outgoing == []
            uow.commit()
        assert _event_types(factory, b_claim.run_id) == before_events
    finally:
        engine.dispose()


def test_real_sqlite_duplicate_reconciliation_is_idempotent(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path, "phase21-step5-idempotent.db")
    try:
        factory = create_unit_of_work_factory(create_session_factory(engine))
        clock = FixedClock()
        registry = _registry()
        gateway = _gateway(tmp_path)
        root_task = CreateTask(factory, clock).execute(
            CreateTaskCommand("A idempotent", "step-5 idempotent reconciliation")
        )
        started = StartRun(factory, clock).execute(StartRunCommand(root_task.task_id))
        assert started.run_id is not None
        agent_b, _revision_b = _active_agent(
            factory, clock, registry, key="step5.idem.b", instructions="mid hop"
        )
        agent_c, _revision_c = _active_agent(
            factory, clock, registry, key="step5.idem.c", instructions="leaf hop"
        )
        ab_id = _dispatch_via_processor(
            factory,
            clock,
            registry,
            gateway,
            _claim(factory, clock, "a-worker"),
            agent_b.key,
            "A delegates to B",
        )
        b_claim = _claim(factory, clock, "b-worker")
        bc_id = _dispatch_via_processor(
            factory, clock, registry, gateway, b_claim, agent_c.key, "B delegates to C"
        )
        c_claim = _claim(factory, clock, "c-worker")
        _finish_via_processor(factory, clock, registry, gateway, c_claim, "c done")

        with factory() as uow:
            first_pass = uow.delegation_requests.get(bc_id)
            assert first_pass is not None and first_pass.status.value == "succeeded"
            before_completed = first_pass.completed_at
            before_events = _event_types(factory, b_claim.run_id)
            uow.commit()

        # Re-run the immediate reconciliation twice more against real
        # persistence; the terminal request is the idempotency fence.
        for _ in range(2):
            with factory() as uow:
                child_run = uow.runs.get(c_claim.run_id)
                assert child_run is not None
                reconcile_child_terminal_in_uow(uow, child_run, clock.now())
                uow.commit()

        with factory() as uow:
            bc = uow.delegation_requests.get(bc_id)
            ab = uow.delegation_requests.get(ab_id)
            row_b = uow.runs.get(b_claim.run_id)
            assert bc is not None
            assert bc.status.value == "succeeded"
            assert bc.completed_at == before_completed
            assert ab is not None and ab.status.value == "dispatched"
            assert row_b is not None and row_b.status is RunStatus.RUNNING
            # One resume, one result, one continuation item — not three.
            assert _event_types(factory, b_claim.run_id) == before_events
            assert before_events.count("run_resumed") == 1
            assert before_events.count("delegation_succeeded") == 1
            assert uow.work_queue.get(b_claim.run_id) is not None
            uow.commit()
        with engine.connect() as connection:
            queue_items = connection.scalar(
                text("SELECT count(*) FROM run_work_items WHERE run_id = :rid"),
                {"rid": str(b_claim.run_id)},
            )
        assert queue_items == 1
    finally:
        engine.dispose()


def test_real_sqlite_depth_boundary_fails_closed_at_the_configured_limit(
    tmp_path: Path,
) -> None:
    engine = _migrated_engine(tmp_path, "phase21-step5-depth.db")
    try:
        factory = create_unit_of_work_factory(create_session_factory(engine))
        clock = FixedClock()
        registry = _registry()
        gateway = _gateway(tmp_path)
        assert MAX_DELEGATION_DEPTH == 3
        root_task = CreateTask(factory, clock).execute(
            CreateTaskCommand("A depth boundary", "step-5 depth boundary")
        )
        started = StartRun(factory, clock).execute(StartRunCommand(root_task.task_id))
        assert started.run_id is not None
        agents = [
            _active_agent(
                factory,
                clock,
                registry,
                key=f"step5.depth.{name}",
                instructions=f"{name} hop",
            )[0]
            for name in ("b", "c", "d", "e")
        ]

        ab_id = _dispatch_via_processor(
            factory,
            clock,
            registry,
            gateway,
            _claim(factory, clock, "a-worker"),
            agents[0].key,
            "A delegates to B",
        )
        b_claim = _claim(factory, clock, "b-worker")
        bc_id = _dispatch_via_processor(
            factory, clock, registry, gateway, b_claim, agents[1].key, "B delegates to C"
        )
        c_claim = _claim(factory, clock, "c-worker")
        cd_id = _dispatch_via_processor(
            factory, clock, registry, gateway, c_claim, agents[2].key, "C delegates to D"
        )
        with factory() as uow:
            for request_id, depth in ((ab_id, 1), (bc_id, 2), (cd_id, 3)):
                request = uow.delegation_requests.get(request_id)
                assert request is not None
                assert request.depth == depth
                assert request.root_delegation_id == ab_id
            uow.commit()

        # D -> E exceeds the bound: the ordinary processor path fails the Run
        # closed with the stable code and materializes nothing.
        d_claim = _claim(factory, clock, "d-worker")
        brain = ScriptedBrain(
            DelegateAction(
                target_agent_key=agents[3].key,
                objective="D delegates to E beyond the bound",
                input_payload={},
                expected_output_contract="never",
                reason="one hop too deep",
            )
        )
        outcome = _processor(factory, clock, registry, brain, gateway).process(_context(d_claim))
        assert outcome.kind == "failed"
        assert outcome.failure is not None
        assert outcome.failure.code == "delegation_depth_exhausted"
        assert outcome.failure.retryable is False

        tasks, runs, delegations = _counts(engine)
        assert (tasks, runs, delegations) == (4, 4, 3)
        with factory() as uow:
            row_d = uow.runs.get(d_claim.run_id)
            assert row_d is not None and row_d.status is RunStatus.RUNNING
            assert row_d.delegation_request_id is None
            d_item = uow.work_queue.get(d_claim.run_id)
            assert d_item is not None and d_item.claimed_by == "d-worker"
            assert uow.delegation_requests.list_for_run(d_claim.run_id) == []
            uow.commit()
    finally:
        engine.dispose()


def test_worker_loop_real_sqlite_nested_chain_closes_through_production_worker_path(
    tmp_path: Path,
) -> None:
    """Primary Step-5A E2E: the full A -> B -> C chain is driven by the
    production WorkerLoop with one shared AgentRunProcessor. Only the
    BrainRuntime is scripted — claiming, outcome application, and delegation
    reconciliation all run through the deployed-worker orchestration."""
    engine = _migrated_engine(tmp_path, "phase21-step5-worker-loop.db")
    try:
        factory = create_unit_of_work_factory(create_session_factory(engine))
        clock = FixedClock()
        registry = _registry()
        gateway = _gateway(tmp_path)
        brain = WorkerScriptedBrain()
        loop, processor = _worker_harness(factory, clock, registry, brain, gateway)

        root_task = CreateTask(factory, clock).execute(
            CreateTaskCommand("A worker-path orchestration", "step-5 worker chain")
        )
        started = StartRun(factory, clock).execute(StartRunCommand(root_task.task_id))
        assert started.run_id is not None
        run_a = started.run_id
        agent_b, revision_b = _active_agent(
            factory,
            clock,
            registry,
            key="step5.wloop.b",
            instructions="Mid-chain agent: consume the child result, then finish.",
        )
        agent_c, revision_c = _active_agent(
            factory,
            clock,
            registry,
            key="step5.wloop.c",
            instructions="Leaf agent: finish with the bounded result.",
        )
        brain.script(
            run_a,
            DelegateAction(
                target_agent_key=agent_b.key,
                objective="A delegates to B",
                input_payload={"hop": "ab"},
                expected_output_contract="Return the bounded result.",
                reason="nested chain hop",
            ),
            FinishAction(summary="a consumed b result", details={"from": "b"}),
        )

        # A is claimed by the worker, proposes the delegation, and the normal
        # worker path materializes AB and parks A.
        assert loop.run_once(processor) is True
        ab_id, run_b = _dispatched_child(factory, run_a)
        brain.script(
            run_b,
            DelegateAction(
                target_agent_key=agent_c.key,
                objective="B delegates to C",
                input_payload={"hop": "bc"},
                expected_output_contract="Return the bounded result.",
                reason="nested chain hop",
            ),
            FinishAction(summary="b consumed c result", details={"from": "c"}),
        )

        # B is claimed through the worker and the same normal path
        # materializes BC and parks B.
        assert loop.run_once(processor) is True
        bc_id, run_c = _dispatched_child(factory, run_b)
        # C's first claim yields through the normal worker outcome so the
        # durable state below is observed while C is genuinely active.
        brain.script(
            run_c,
            YieldAction(delay_seconds=0, reason="leaf hop warm-up"),
            FinishAction(summary="c evidence complete", details={"from": "c"}),
        )

        # C is claimed through the worker; the yield outcome requeues it while
        # it stays RUNNING.
        assert loop.run_once(processor) is True

        # Required durable state while C is active: each Run waits only on its
        # own immediate DelegationRequest.
        with factory() as uow:
            row_a = uow.runs.get(run_a)
            row_b = uow.runs.get(run_b)
            row_c = uow.runs.get(run_c)
            assert row_a is not None and row_a.status is RunStatus.WAITING_FOR_DELEGATION
            assert row_a.delegation_request_id == ab_id
            assert row_b is not None and row_b.status is RunStatus.WAITING_FOR_DELEGATION
            assert row_b.delegation_request_id == bc_id
            assert row_c is not None and row_c.status is RunStatus.RUNNING
            assert uow.work_queue.get(run_c) is not None
            uow.commit()

        # C finishes normally; normal outcome application terminalizes it and
        # normal reconciliation resolves BC and resumes/enqueues B.
        assert loop.run_once(processor) is True
        with factory() as uow:
            bc = uow.delegation_requests.get(bc_id)
            row_a = uow.runs.get(run_a)
            row_b = uow.runs.get(run_b)
            row_c = uow.runs.get(run_c)
            assert bc is not None and bc.status.value == "succeeded"
            assert row_c is not None and row_c.status is RunStatus.SUCCEEDED
            assert row_b is not None and row_b.status is RunStatus.RUNNING
            assert row_b.delegation_request_id is None
            assert uow.work_queue.get(run_b) is not None
            # A is untouched until B terminalizes.
            assert row_a is not None and row_a.status is RunStatus.WAITING_FOR_DELEGATION
            assert row_a.delegation_request_id == ab_id
            assert uow.work_queue.get(run_a) is None
            uow.commit()

        # B is claimed again through the worker, receives C's durable bounded
        # result in its normal runtime context, and finishes normally.
        assert loop.run_once(processor) is True
        b_requests = brain.requests_for(run_b)
        assert len(b_requests) == 2
        assert "# DELEGATIONS" in b_requests[-1].context
        assert "summary=c evidence complete" in b_requests[-1].context

        # Only after B terminalizes does AB resolve and A resume.
        with factory() as uow:
            ab = uow.delegation_requests.get(ab_id)
            row_a = uow.runs.get(run_a)
            row_b = uow.runs.get(run_b)
            assert ab is not None and ab.status.value == "succeeded"
            assert row_b is not None and row_b.status is RunStatus.SUCCEEDED
            assert row_a is not None and row_a.status is RunStatus.RUNNING
            assert row_a.delegation_request_id is None
            assert uow.work_queue.get(run_a) is not None
            uow.commit()

        # A is claimed again through the worker, receives B's durable bounded
        # result, and finishes normally. The queue then drains.
        assert loop.run_once(processor) is True
        a_requests = brain.requests_for(run_a)
        assert len(a_requests) == 2
        assert "# DELEGATIONS" in a_requests[-1].context
        assert "summary=b consumed c result" in a_requests[-1].context
        assert loop.run_once(processor) is False

        # C's own first request carried its delegated input context.
        c_requests = brain.requests_for(run_c)
        assert len(c_requests) == 2
        assert "# DELEGATED WORK" in c_requests[0].context

        with factory() as uow:
            ab = uow.delegation_requests.get(ab_id)
            bc = uow.delegation_requests.get(bc_id)
            assert ab is not None and bc is not None
            assert ab.parent_run_id == run_a
            assert bc.parent_run_id == run_b
            assert ab.depth == 1 and ab.root_delegation_id == ab.id
            assert bc.depth == 2 and bc.root_delegation_id == ab.id
            assert ab.status.value == "succeeded" and bc.status.value == "succeeded"
            assert ab.child_run_id == run_b and bc.child_run_id == run_c
            assert uow.delegation_requests.list_for_run(run_a) == [ab]
            assert uow.delegation_requests.list_for_run(run_b) == [bc]
            assert uow.delegation_requests.list_for_run(run_c) == []
            # Exact Agent freeze exists for each delegated child.
            b_resolution = uow.run_agent_resolutions.get(run_b)
            c_resolution = uow.run_agent_resolutions.get(run_c)
            assert b_resolution is not None
            assert b_resolution.agent_id == agent_b.id
            assert b_resolution.revision_id == revision_b.id
            assert c_resolution is not None
            assert c_resolution.agent_id == agent_c.id
            assert c_resolution.revision_id == revision_c.id
            # Nothing survives in the queue: terminal runs have no items.
            assert uow.work_queue.get(run_a) is None
            assert uow.work_queue.get(run_b) is None
            assert uow.work_queue.get(run_c) is None
            uow.commit()
        with engine.connect() as connection:
            queue_items = connection.scalar(text("SELECT count(*) FROM run_work_items"))
        assert queue_items == 0

        # Exactly one request per hop, one delegated child Task/Run per hop.
        tasks, runs, delegations = _counts(engine)
        assert (tasks, runs, delegations) == (3, 3, 2)

        # No duplicated lifecycle transitions anywhere in the chain.
        a_events = _event_types(factory, run_a)
        b_events = _event_types(factory, run_b)
        c_events = _event_types(factory, run_c)
        for events in (a_events, b_events):
            assert events.count("run_started") == 1
            assert events.count("delegation_dispatched") == 1
            assert events.count("run_waiting_for_delegation") == 1
            assert events.count("delegation_succeeded") == 1
            assert events.count("run_resumed") == 1
            assert events.count("agent_finished") == 1
            assert events.count("run_succeeded") == 1
        assert c_events.count("run_started") == 1
        assert c_events.count("delegation_dispatched") == 0
        assert c_events.count("run_resumed") == 0
        assert c_events.count("agent_finished") == 1
        assert c_events.count("run_succeeded") == 1
    finally:
        engine.dispose()


def test_worker_loop_real_sqlite_depth_exhaustion_fails_closed_through_worker_path(
    tmp_path: Path,
) -> None:
    """Depth exhaustion is driven through the worker too: D's failed
    processing outcome is applied by the normal worker path, so the durable
    Run terminalizes instead of lingering RUNNING, and zero E state is
    materialized."""
    engine = _migrated_engine(tmp_path, "phase21-step5-worker-depth.db")
    try:
        factory = create_unit_of_work_factory(create_session_factory(engine))
        clock = FixedClock()
        registry = _registry()
        gateway = _gateway(tmp_path)
        brain = WorkerScriptedBrain()
        loop, processor = _worker_harness(factory, clock, registry, brain, gateway)
        assert MAX_DELEGATION_DEPTH == 3

        root_task = CreateTask(factory, clock).execute(
            CreateTaskCommand("A worker-path depth", "step-5 worker depth boundary")
        )
        started = StartRun(factory, clock).execute(StartRunCommand(root_task.task_id))
        assert started.run_id is not None
        run_a = started.run_id
        agents = [
            _active_agent(
                factory,
                clock,
                registry,
                key=f"step5.wdepth.{name}",
                instructions=f"{name} hop",
            )[0]
            for name in ("b", "c", "d", "e")
        ]

        def _delegate_to(agent: object) -> DelegateAction:
            assert isinstance(agent, Agent)
            return DelegateAction(
                target_agent_key=agent.key,
                objective=f"chain hop to {agent.key}",
                input_payload={},
                expected_output_contract="bounded result",
                reason="depth chain hop",
            )

        brain.script(run_a, _delegate_to(agents[0]))
        assert loop.run_once(processor) is True
        ab_id, run_b = _dispatched_child(factory, run_a)
        brain.script(run_b, _delegate_to(agents[1]))
        assert loop.run_once(processor) is True
        bc_id, run_c = _dispatched_child(factory, run_b)
        brain.script(run_c, _delegate_to(agents[2]))
        assert loop.run_once(processor) is True
        cd_id, run_d = _dispatched_child(factory, run_c)
        with factory() as uow:
            for request_id, depth in ((ab_id, 1), (bc_id, 2), (cd_id, 3)):
                request = uow.delegation_requests.get(request_id)
                assert request is not None
                assert request.depth == depth
                assert request.root_delegation_id == ab_id
            uow.commit()

        # D -> E exceeds the bound: the worker claims D, the ordinary
        # processor path fails closed, and the normal worker path applies the
        # failure so the durable Run state reflects production behavior.
        brain.script(
            run_d,
            DelegateAction(
                target_agent_key=agents[3].key,
                objective="D delegates to E beyond the bound",
                input_payload={},
                expected_output_contract="never",
                reason="one hop too deep",
            ),
        )
        assert loop.run_once(processor) is True

        with factory() as uow:
            row_d = uow.runs.get(run_d)
            assert row_d is not None
            assert row_d.status is RunStatus.FAILED
            assert row_d.failure is not None
            assert row_d.failure.code == "delegation_depth_exhausted"
            assert row_d.failure.retryable is False
            assert uow.work_queue.get(run_d) is None
            assert uow.delegation_requests.list_for_run(run_d) == []
            cd = uow.delegation_requests.get(cd_id)
            assert cd is not None
            assert cd.status.value == "failed"
            assert cd.failure_code == "delegation_depth_exhausted"
            # Normal reconciliation resolved CD and resumed C.
            row_c = uow.runs.get(run_c)
            assert row_c is not None and row_c.status is RunStatus.RUNNING
            assert row_c.delegation_request_id is None
            assert uow.work_queue.get(run_c) is not None
            # A is still parked on AB: only B terminalizing can wake it.
            row_a = uow.runs.get(run_a)
            assert row_a is not None and row_a.status is RunStatus.WAITING_FOR_DELEGATION
            assert row_a.delegation_request_id == ab_id
            assert uow.work_queue.get(run_a) is None
            uow.commit()

        # Zero E materialization: no fourth delegated Task/Run/request.
        tasks, runs, delegations = _counts(engine)
        assert (tasks, runs, delegations) == (4, 4, 3)
        d_events = _event_types(factory, run_d)
        assert d_events.count("run_failed") == 1
        c_events = _event_types(factory, run_c)
        assert c_events.count("delegation_failed") == 1
        assert c_events.count("run_resumed") == 1
    finally:
        engine.dispose()
