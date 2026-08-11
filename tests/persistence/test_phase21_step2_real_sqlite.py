"""Real SQLite closure proofs for Phase 21 / Step 2.

Only the BrainRuntime is scripted.  Claims, Agent resolution, delegation
repositories, approval coordination, ToolInvocation lifecycle, work queue,
and outcome appliers all use the production implementations.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

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
from friday.application.commands import (
    ApproveRequestCommand,
    CreateTaskCommand,
    RetryFailedRunCommand,
    StartRunCommand,
)
from friday.application.create_task import CreateTask
from friday.application.delegation import DispatchDelegation
from friday.application.errors import DelegatedManualRetryForbidden
from friday.application.lifecycle import RetryFailedRun
from friday.application.ports import UnitOfWorkFactory
from friday.application.results import RunClaimResult
from friday.application.retry_policy import RetryPolicy
from friday.application.run_processor import ClaimContext
from friday.application.runtime_actions import DelegateAction, FinishAction, InvokeToolAction
from friday.application.start_run import StartRun
from friday.application.tool_authorization import RequestToolApproval
from friday.application.worker_coordination import (
    ApplyFailedOutcome,
    ApplySucceededOutcome,
    ApplyWaitingForDelegationOutcome,
    ClaimNextRun,
    VerifyRunClaim,
)
from friday.domain.agent import Agent, AgentRevision, AgentRevisionSourceKind
from friday.domain.failure import Failure, FailureCause
from friday.domain.run import RunStatus
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory
from friday.infrastructure.tools.gateway import WorkspaceToolGateway, WorkspaceToolGatewaySettings

REPO_ROOT = Path(__file__).resolve().parents[2]
AT = datetime(2026, 8, 9, 12, tzinfo=UTC)
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
        assert isinstance(action, (DelegateAction, FinishAction, InvokeToolAction))
        return BrainResponse(action=action)


def _registry() -> BrainRuntimeRegistry:
    registry = BrainRuntimeRegistry()
    registry.register("claude_cli", lambda: None)  # type: ignore[arg-type,return-value]
    return registry


def _migrated_engine(tmp_path: Path) -> Engine:
    db_path = tmp_path / "phase21-step2.db"
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
        key=key, display_name=key.title(), description="delegation E2E target"
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


def test_real_sqlite_delegation_runs_through_claim_resolution_and_parent_resume(
    tmp_path: Path,
) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        factory = create_unit_of_work_factory(create_session_factory(engine))
        clock = FixedClock()
        registry = _registry()
        gateway = _gateway(tmp_path)
        parent_task = CreateTask(factory, clock).execute(
            CreateTaskCommand("Parent orchestration", "delegate bounded evidence work")
        )
        parent = StartRun(factory, clock).execute(StartRunCommand(parent_task.task_id))
        assert parent.run_id is not None
        target, target_revision = _active_agent(
            factory,
            clock,
            registry,
            key="step2.researcher",
            instructions="Use the exact delegated input and return durable evidence.",
        )

        parent_brain = ScriptedBrain(
            DelegateAction(
                target_agent_key=target.key,
                objective="Gather release evidence.",
                input_payload={"release": "21.2", "paths": ["CHANGELOG.md"]},
                expected_output_contract="Return cited findings.",
                reason="specialist",
            ),
            FinishAction(summary="parent consumed child result", details={"ok": True}),
        )
        parent_claim = _claim(factory, clock, "parent-worker")
        assert parent_claim.run_id == parent.run_id
        parent_outcome = _processor(factory, clock, registry, parent_brain, gateway).process(
            _context(parent_claim)
        )
        assert parent_outcome.kind == "waiting_for_delegation"
        assert parent_outcome.delegation_request_id is not None
        ApplyWaitingForDelegationOutcome(factory, clock).execute(
            parent_claim.run_id,
            parent_claim.worker_id,
            parent_claim.claim_token,
            parent_claim.claim_generation,
            parent_outcome.delegation_request_id,
        )

        with factory() as uow:
            request = uow.delegation_requests.get(parent_outcome.delegation_request_id)
            assert request is not None
            assert request.child_task_id is not None and request.child_run_id is not None
            binding = uow.task_agent_bindings.get(request.child_task_id)
            assert binding is not None and binding.agent_id == request.target_agent_id == target.id
            child_id = request.child_run_id
            uow.commit()

        child_brain = ScriptedBrain(FinishAction(summary="evidence found", details={"sources": 2}))
        child_claim = _claim(factory, clock, "child-worker")
        assert child_claim.run_id == child_id
        child_outcome = _processor(factory, clock, registry, child_brain, gateway).process(
            _context(child_claim)
        )
        assert child_outcome.kind == "succeeded"
        assert child_brain.requests[0].run_id == child_id
        assert "# AGENT" in child_brain.requests[0].context
        assert target_revision.instructions in child_brain.requests[0].context
        assert "# DELEGATED WORK" in child_brain.requests[0].context
        assert '"release":"21.2"' in child_brain.requests[0].context

        with factory() as uow:
            resolution = uow.run_agent_resolutions.get(child_id)
            assert resolution is not None
            assert resolution.agent_id == target.id
            assert resolution.revision_id == target_revision.id
            uow.commit()

        ApplySucceededOutcome(factory, clock).execute(
            child_claim.run_id,
            child_claim.worker_id,
            child_claim.claim_token,
            child_claim.claim_generation,
            child_outcome.final_response,
        )

        with factory() as uow:
            request = uow.delegation_requests.get(parent_outcome.delegation_request_id)
            parent_row = uow.runs.get(parent.run_id)
            assert request is not None and request.status.value == "succeeded"
            assert parent_row is not None and parent_row.status is RunStatus.RUNNING
            assert uow.work_queue.get(parent.run_id) is not None
            uow.commit()

        resumed_claim = _claim(factory, clock, "parent-resumed-worker")
        assert resumed_claim.run_id == parent.run_id
        resumed_brain = _processor(factory, clock, registry, parent_brain, gateway)
        resumed_outcome = resumed_brain.process(_context(resumed_claim))
        assert resumed_outcome.kind == "succeeded"
        assert "# DELEGATIONS" in parent_brain.requests[-1].context
        assert "summary=evidence found" in parent_brain.requests[-1].context
        ApplySucceededOutcome(factory, clock).execute(
            resumed_claim.run_id,
            resumed_claim.worker_id,
            resumed_claim.claim_token,
            resumed_claim.claim_generation,
            resumed_outcome.final_response,
        )

        with factory() as uow:
            completed = uow.runs.get(parent.run_id)
            assert completed is not None and completed.status is RunStatus.SUCCEEDED
            assert all(request.run_id == parent.run_id for request in parent_brain.requests)
            assert all(request.run_id == child_id for request in child_brain.requests)
            uow.commit()
    finally:
        engine.dispose()


def test_real_sqlite_delegated_child_has_independent_protected_tool_authority(
    tmp_path: Path,
) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        factory = create_unit_of_work_factory(create_session_factory(engine))
        clock = FixedClock()
        registry = _registry()
        gateway = _gateway(tmp_path)
        write = InvokeToolAction(
            tool="workspace.write_text",
            tool_input={"path": "authority-proof.txt", "content": "parent", "overwrite": True},
            reason="protected proof",
        )
        parent_task = CreateTask(factory, clock).execute(CreateTaskCommand("Parent", ""))
        parent = StartRun(factory, clock).execute(StartRunCommand(parent_task.task_id))
        assert parent.run_id is not None
        target, _revision = _active_agent(
            factory,
            clock,
            registry,
            key="step2.authority-child",
            instructions="Child instructions do not inherit parent approvals.",
        )
        parent_brain = ScriptedBrain(
            write,
            write,
            DelegateAction(
                target_agent_key=target.key,
                objective="Propose the same protected write.",
                input_payload={"same": "tool"},
                expected_output_contract="done",
                reason="authority proof",
            ),
            FinishAction(summary="parent done"),
        )
        parent_claim = _claim(factory, clock, "authority-parent")
        processor = _processor(factory, clock, registry, parent_brain, gateway)
        waiting = processor.process(_context(parent_claim))
        assert waiting.kind == "waiting_for_approval"
        assert waiting.approval_request_id is not None
        ApproveRequest(factory, clock).execute(
            ApproveRequestCommand(waiting.approval_request_id, resolver="operator")
        )
        parent_claim = _claim(factory, clock, "authority-parent-resumed")
        waiting_for_child = processor.process(_context(parent_claim))
        assert waiting_for_child.kind == "waiting_for_delegation"
        assert waiting_for_child.delegation_request_id is not None

        with factory() as uow:
            parent_approvals = uow.approvals.list_for_run(parent.run_id)
            parent_invocations = uow.tool_invocations.list_for_run(parent.run_id)
            request = uow.delegation_requests.get(waiting_for_child.delegation_request_id)
            assert request is not None and request.child_run_id is not None
            child_id = request.child_run_id
            assert len(parent_approvals) == 1 and parent_approvals[0].is_consumed
            assert len(parent_invocations) == 1
            uow.commit()

        child_brain = ScriptedBrain(write, write, FinishAction(summary="child done"))
        child_claim = _claim(factory, clock, "authority-child")
        child_processor = _processor(factory, clock, registry, child_brain, gateway)
        child_waiting = child_processor.process(_context(child_claim))
        assert child_waiting.kind == "waiting_for_approval"
        assert child_waiting.approval_request_id is not None

        with factory() as uow:
            child_approvals = uow.approvals.list_for_run(child_id)
            assert len(child_approvals) == 1
            assert child_approvals[0].status.value == "pending"
            assert child_approvals[0].run_id == child_id
            assert (
                child_approvals[0].authorization_fingerprint
                != uow.approvals.list_for_run(parent.run_id)[0].authorization_fingerprint
            )
            assert uow.tool_invocations.list_for_run(child_id) == []
            uow.commit()

        ApproveRequest(factory, clock).execute(
            ApproveRequestCommand(child_waiting.approval_request_id, resolver="operator")
        )
        child_claim = _claim(factory, clock, "authority-child-resumed")
        child_done = child_processor.process(_context(child_claim))
        assert child_done.kind == "succeeded"
        ApplySucceededOutcome(factory, clock).execute(
            child_claim.run_id,
            child_claim.worker_id,
            child_claim.claim_token,
            child_claim.claim_generation,
            child_done.final_response,
        )
        with factory() as uow:
            invocations = uow.tool_invocations.list_for_run(child_id)
            assert len(invocations) == 1
            assert invocations[0].approval_request_id == child_waiting.approval_request_id
            assert invocations[0].run_id != parent.run_id
            uow.commit()
    finally:
        engine.dispose()


def test_real_sqlite_delegated_retries_stay_in_lineage_and_manual_retry_is_rejected(
    tmp_path: Path,
) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        factory = create_unit_of_work_factory(create_session_factory(engine))
        clock = FixedClock()
        registry = _registry()
        parent_task = CreateTask(factory, clock).execute(CreateTaskCommand("Parent", ""))
        parent = StartRun(factory, clock).execute(StartRunCommand(parent_task.task_id))
        assert parent.run_id is not None
        target, target_revision = _active_agent(
            factory,
            clock,
            registry,
            key="step2.retry-target",
            instructions="Retry target",
        )
        parent_claim = _claim(factory, clock, "retry-parent")
        request = DispatchDelegation(factory, clock, registry).execute(
            parent_run_id=parent_claim.run_id,
            worker_id=parent_claim.worker_id,
            claim_token=parent_claim.claim_token,
            claim_generation=parent_claim.claim_generation,
            target_agent_key=target.key,
            objective="retry bounded work",
            input_payload={"retry": True},
            expected_output_contract="result",
        )
        assert request.child_run_id is not None
        child_execution_id = request.child_run_id
        child_claim = _claim(factory, clock, "retry-child-1")
        resolution = ResolveRunAgent(factory, clock, registry).execute(
            child_claim.run_id,
            child_claim.worker_id,
            child_claim.claim_token,
            child_claim.claim_generation,
        )
        assert resolution is not None
        assert resolution.agent_id == target.id and resolution.revision_id == target_revision.id
        policy = RetryPolicy(2, timedelta(seconds=1), 1, timedelta(seconds=1))
        failure = Failure("worker_timeout", "worker timed out", True, FailureCause.TIMEOUT)
        # Keep attempt ordering deterministic: the retry must be created after
        # the source when SQLite orders same-lineage runs by timestamp/id.
        clock.advance(timedelta(seconds=1))
        ApplyFailedOutcome(factory, clock, retry_policy=policy).execute(
            child_claim.run_id,
            child_claim.worker_id,
            child_claim.claim_token,
            child_claim.claim_generation,
            failure,
        )

        with factory() as uow:
            persisted = uow.delegation_requests.get(request.id)
            parent_row = uow.runs.get(parent.run_id)
            attempts = uow.runs.list_for_execution(child_claim.run_id)
            retry = attempts[-1]
            inherited = uow.run_agent_resolutions.get(retry.id)
            assert persisted is not None and persisted.status.value == "dispatched"
            assert parent_row is not None and parent_row.status is RunStatus.WAITING_FOR_DELEGATION
            assert uow.work_queue.get(retry.id) is not None
            assert inherited is not None
            assert inherited.agent_id == resolution.agent_id
            assert inherited.revision_id == resolution.revision_id
            uow.commit()

        clock.advance(timedelta(seconds=2))
        retry_claim = _claim(factory, clock, "retry-child-2")
        ApplyFailedOutcome(factory, clock, retry_policy=policy).execute(
            retry_claim.run_id,
            retry_claim.worker_id,
            retry_claim.claim_token,
            retry_claim.claim_generation,
            failure,
        )
        with factory() as uow:
            persisted = uow.delegation_requests.get(request.id)
            parent_row = uow.runs.get(parent.run_id)
            assert persisted is not None and persisted.status.value == "failed"
            assert persisted.failure_code == "worker_timeout"
            assert parent_row is not None and parent_row.status is RunStatus.RUNNING
            assert uow.work_queue.get(parent.run_id) is not None
            before_runs = [
                (run.id, run.status, run.execution_id)
                for run in uow.runs.list_for_execution(child_execution_id)
            ]
            before_request = (persisted.status, persisted.failure_code, persisted.completed_at)
            before_parent = (parent_row.status, parent_row.delegation_request_id)
            before_queue = uow.work_queue.get(parent.run_id)
            uow.commit()

        with pytest.raises(DelegatedManualRetryForbidden, match="delegated_manual_retry_forbidden"):
            RetryFailedRun(factory, clock).execute(RetryFailedRunCommand(retry_claim.run_id))

        with factory() as uow:
            persisted = uow.delegation_requests.get(request.id)
            parent_row = uow.runs.get(parent.run_id)
            after_runs = [
                (run.id, run.status, run.execution_id)
                for run in uow.runs.list_for_execution(child_execution_id)
            ]
            assert after_runs == before_runs
            assert persisted is not None
            assert (
                persisted.status,
                persisted.failure_code,
                persisted.completed_at,
            ) == before_request
            assert parent_row is not None
            assert (parent_row.status, parent_row.delegation_request_id) == before_parent
            assert uow.work_queue.get(parent.run_id) == before_queue
            assert after_runs == before_runs
            uow.commit()
    finally:
        engine.dispose()
