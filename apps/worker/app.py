"""Worker composition root: settings, infrastructure, use cases, processor,
and loop. Construction is fail-closed: a missing Claude executable, an
unverifiable brain-only CLI, or an invalid workspace root raises before any
Worker exists — no claim can ever happen without a real processor."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine

from apps.worker.runtime_settings import RuntimeSettings
from apps.worker.settings import WorkerSettings
from apps.worker.worker_loop import WorkerLoop
from friday.application.agent_run_processor import AgentRunProcessor, RuntimeLimits
from friday.application.claim_aware_tool_execution import ExecuteToolAction
from friday.application.retry_policy import RetryPolicy
from friday.application.tool_authorization import RequestToolApproval
from friday.application.worker_coordination import (
    ApplyFailedOutcome,
    ApplySucceededOutcome,
    ApplyWaitingOutcome,
    ClaimNextRun,
    RenewRunLease,
    RequeueClaimedRun,
    VerifyRunClaim,
)
from friday.application.worker_maintenance import ExpireDueApprovals, RecoverExpiredLeases
from friday.infrastructure.brain.claude_cli import (
    ClaudeCliBrainRuntime,
    ClaudeCliSettings,
    verify_brain_only_support,
)
from friday.infrastructure.clock import SystemClock
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory
from friday.infrastructure.tools.gateway import (
    WorkspaceToolGateway,
    WorkspaceToolGatewaySettings,
)


@dataclass(slots=True)
class Worker:
    engine: Engine
    settings: WorkerSettings
    loop: WorkerLoop
    processor: AgentRunProcessor


def create_worker(settings: WorkerSettings, runtime: RuntimeSettings) -> Worker:
    # --- fail-closed environment verification (before anything else) ------
    claude_settings = ClaudeCliSettings(
        executable=runtime.claude_executable,
        model=runtime.claude_model,
        timeout_seconds=runtime.claude_timeout_seconds,
        max_output_bytes=runtime.claude_max_output_bytes,
    )
    verify_brain_only_support(claude_settings)  # raises BrainUnavailable
    gateway = WorkspaceToolGateway(  # raises WorkspaceAccessDenied
        WorkspaceToolGatewaySettings(
            workspace_root=runtime.workspace_root,
            max_file_bytes=runtime.tool_max_file_bytes,
            max_list_entries=runtime.tool_max_list_entries,
            process_timeout_seconds=runtime.tool_timeout_seconds,
            process_max_timeout_seconds=runtime.tool_max_timeout_seconds,
            max_stdout_bytes=runtime.tool_max_stdout_bytes,
            max_stderr_bytes=runtime.tool_max_stderr_bytes,
        )
    )

    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    uow_factory = create_unit_of_work_factory(session_factory)
    clock = SystemClock()
    retry_policy = RetryPolicy(
        max_attempts=settings.retry_max_attempts,
        base_delay=settings.retry_base_delay,
        multiplier=settings.retry_multiplier,
        max_delay=settings.retry_max_delay,
    )

    brain = ClaudeCliBrainRuntime(claude_settings)
    processor = AgentRunProcessor(
        uow_factory=uow_factory,
        clock=clock,
        brain=brain,
        gateway=gateway,
        verify_claim=VerifyRunClaim(uow_factory, clock),
        request_tool_approval=RequestToolApproval(uow_factory, clock),
        execute_tool_action=ExecuteToolAction(uow_factory, clock, gateway),
        limits=RuntimeLimits(
            max_turns_per_claim=runtime.max_turns_per_claim,
            max_tool_calls_per_claim=runtime.max_tool_calls_per_claim,
            max_context_chars=runtime.max_context_chars,
            max_response_bytes=runtime.max_response_bytes,
            max_yield_seconds=runtime.max_yield_seconds,
        ),
    )

    loop = WorkerLoop(
        claim_next_run=ClaimNextRun(
            uow_factory,
            clock,
            worker_id=settings.worker_id,
            lease_duration=settings.lease_duration,
            candidate_limit=settings.candidate_limit,
        ),
        renew_lease=RenewRunLease(uow_factory, clock, lease_duration=settings.lease_duration),
        requeue_claimed_run=RequeueClaimedRun(uow_factory, clock),
        apply_failed=ApplyFailedOutcome(uow_factory, clock, retry_policy=retry_policy),
        apply_succeeded=ApplySucceededOutcome(uow_factory, clock),
        apply_waiting=ApplyWaitingOutcome(uow_factory, clock),
        recover_expired_leases=RecoverExpiredLeases(
            uow_factory, clock, batch_size=settings.maintenance_batch_size
        ),
        expire_due_approvals=ExpireDueApprovals(
            uow_factory, clock, batch_size=settings.maintenance_batch_size
        ),
        heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
        maintenance_interval_seconds=settings.maintenance_interval_seconds,
        poll_interval_seconds=settings.poll_interval_seconds,
    )
    return Worker(engine=engine, settings=settings, loop=loop, processor=processor)
