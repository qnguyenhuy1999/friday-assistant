"""AgentRunProcessor — the vendor-neutral RunProcessor that drives a Run
through bounded brain turns.

Per claim, the loop is:

    for turn in 1..max_turns_per_claim:
        verify claim (cheap flag + durable check)
        load a fresh durable snapshot (short read transaction)
        build the deterministic bounded context
        call the brain               # outside any transaction
        verify claim again
        dispatch the proposed action

The brain only proposes; every durable effect goes through claim-fenced
use cases (RequestToolApproval, ExecuteToolAction), and the final Run
transition stays with Phase 10's Apply* outcome appliers — this processor
never marks a Run succeeded/failed itself, it only returns an outcome.

Claim loss at any checkpoint returns `yielded(now)`: the worker loop
discards outcomes for lost leases, and RequeueClaimedRun is itself fenced,
so a stale worker can never move durable state.

Failure policy (stable codes, bounded messages):
    agent_reported_failure    brain chose the fail action (not retryable)
    brain_response_invalid    repair budget exhausted (retryable)
    brain_timeout             CLI exceeded its deadline (retryable)
    brain_unavailable         CLI missing/crashed (retryable)
    brain_protocol_error      unparseable CLI envelope (retryable)
    tool_not_found            brain invented an unregistered tool (retryable)
    tool_execution_ambiguous  prior protected execution not terminal (not retryable)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from friday.application.brain_runtime import BrainRequest, BrainResponse, BrainRuntime
from friday.application.claim_aware_tool_execution import ExecuteToolAction
from friday.application.commands import RequestApprovalCommand
from friday.application.errors import (
    BrainProtocolError,
    BrainResponseInvalid,
    BrainTimeout,
    BrainUnavailable,
    ClaimLost,
    ToolExecutionAmbiguous,
    ToolInputInvalid,
    ToolNotFound,
)
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.run_processor import ClaimContext, ProcessingOutcome
from friday.application.runtime_actions import (
    BrainAction,
    FailAction,
    FinishAction,
    InvokeToolAction,
    YieldAction,
)
from friday.application.runtime_context import (
    MIN_CONTEXT_CHARS,
    RunSnapshot,
    build_runtime_context,
)
from friday.application.tool_authorization import (
    RequestToolApproval,
    compute_authorization_fingerprint,
)
from friday.application.tool_gateway import ToolCall, ToolGateway
from friday.application.worker_coordination import VerifyRunClaim
from friday.domain.failure import Failure, FailureCause
from friday.domain.run import RunStatus
from friday.domain.step import TERMINAL_RUN_STEP_STATUSES
from friday.domain.tool import TERMINAL_TOOL_INVOCATION_STATUSES

_MAX_TURN_NOTE_CHARS = 500
_MAX_RECENT_EVENTS = 50


@dataclass(frozen=True, slots=True)
class RuntimeLimits:
    max_turns_per_claim: int
    max_tool_calls_per_claim: int
    max_context_chars: int
    max_response_bytes: int
    max_yield_seconds: int

    def __post_init__(self) -> None:
        if self.max_turns_per_claim < 1:
            raise ValueError("max_turns_per_claim must be >= 1")
        if self.max_tool_calls_per_claim < 1:
            raise ValueError("max_tool_calls_per_claim must be >= 1")
        if self.max_context_chars < MIN_CONTEXT_CHARS:
            raise ValueError(f"max_context_chars must be >= {MIN_CONTEXT_CHARS}")
        if self.max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        if self.max_yield_seconds < 0:
            raise ValueError("max_yield_seconds must be >= 0")


class AgentRunProcessor:
    """Satisfies Phase 10's RunProcessor protocol with a real agent loop."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        brain: BrainRuntime,
        gateway: ToolGateway,
        verify_claim: VerifyRunClaim,
        request_tool_approval: RequestToolApproval,
        execute_tool_action: ExecuteToolAction,
        limits: RuntimeLimits,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._brain = brain
        self._gateway = gateway
        self._verify_claim = verify_claim
        self._request_tool_approval = request_tool_approval
        self._execute_tool_action = execute_tool_action
        self._limits = limits

    # ------------------------------------------------------------------ API

    def process(self, context: ClaimContext) -> ProcessingOutcome:
        turn_notes: list[str] = []
        tool_calls = 0

        for turn in range(1, self._limits.max_turns_per_claim + 1):
            if not self._claim_holds(context):
                return self._yield_now()

            snapshot = self._load_snapshot(context, tuple(turn_notes))
            if snapshot is None:
                return self._yield_now()

            document = build_runtime_context(
                snapshot,
                tool_manifest=self._gateway.list_tools(),
                attempt_number=context.attempt_number,
                turn_number=turn,
                max_chars=self._limits.max_context_chars,
            )
            request = BrainRequest(
                run_id=context.run_id,
                task_id=context.task_id,
                turn_number=turn,
                attempt_number=context.attempt_number,
                context=document,
                tool_manifest=self._gateway.list_tools(),
                max_response_bytes=self._limits.max_response_bytes,
            )

            try:
                response = self._brain.next_action(request)  # outside any txn
            except BrainResponseInvalid as exc:
                return self._failed("brain_response_invalid", str(exc), retryable=True)
            except BrainTimeout as exc:
                return self._failed(
                    "brain_timeout", str(exc), retryable=True, cause=FailureCause.TIMEOUT
                )
            except BrainUnavailable as exc:
                return self._failed("brain_unavailable", str(exc), retryable=True)
            except BrainProtocolError as exc:
                return self._failed("brain_protocol_error", str(exc), retryable=True)

            if not self._claim_holds(context):
                return self._yield_now()  # never act on a response for a lost claim

            outcome, note, tool_call_used = self._dispatch(context, response, snapshot)
            if outcome is not None:
                return outcome
            if note is not None:
                turn_notes.append(note[:_MAX_TURN_NOTE_CHARS])
            if tool_call_used:
                tool_calls += 1
                if tool_calls >= self._limits.max_tool_calls_per_claim:
                    return self._yield_now()  # tool budget: continue under a fresh claim

        return self._yield_now()  # turn budget: continue under a fresh claim

    # ------------------------------------------------------------ dispatch

    def _dispatch(
        self,
        context: ClaimContext,
        response: BrainResponse,
        snapshot: RunSnapshot,
    ) -> tuple[ProcessingOutcome | None, str | None, bool]:
        """Returns (final outcome | None to continue, turn note, tool used)."""
        action: BrainAction = response.action
        if isinstance(action, FinishAction):
            blocker = self._finish_blocker(snapshot)
            if blocker is not None:
                return None, f"finish rejected: {blocker}", False
            return ProcessingOutcome.succeeded(), None, False

        if isinstance(action, FailAction):
            return (
                self._failed(
                    "agent_reported_failure",
                    action.reason,
                    retryable=False,
                ),
                None,
                False,
            )

        if isinstance(action, YieldAction):
            delay = min(action.delay_seconds or 0, self._limits.max_yield_seconds)
            available_at = self._clock.now() + timedelta(seconds=delay)
            return ProcessingOutcome.yielded(available_at), None, False

        return self._dispatch_tool(context, action)

    def _dispatch_tool(
        self, context: ClaimContext, action: InvokeToolAction
    ) -> tuple[ProcessingOutcome | None, str | None, bool]:
        try:
            call = ToolCall(tool=action.tool, tool_input=action.tool_input)
        except ToolInputInvalid as exc:
            return None, f"invalid tool input for {action.tool}: {exc}", False

        try:
            result = self._execute_tool_action.execute(
                run_id=context.run_id,
                step_id=None,
                call=call,
                worker_id=context.worker_id,
                claim_token=context.claim_token,
                claim_generation=context.claim_generation,
            )
        except ToolNotFound:
            return (
                self._failed(
                    "tool_not_found",
                    f"brain proposed an unregistered tool: {action.tool}",
                    retryable=True,
                    cause=FailureCause.TOOL,
                ),
                None,
                False,
            )
        except ToolExecutionAmbiguous as exc:
            return (
                self._failed(
                    "tool_execution_ambiguous",
                    str(exc),
                    retryable=False,
                    cause=FailureCause.TOOL,
                ),
                None,
                False,
            )
        except ClaimLost:
            return self._yield_now(), None, False

        if result.kind == "approval_required":
            return self._request_approval(context, action, call), None, False

        status = result.result.status if result.result is not None else "unknown"
        note = f"invoked {call.tool} -> {status}"
        if result.replayed:
            note += " (replayed durable result)"
        return None, note, True

    def _request_approval(
        self, context: ClaimContext, action: InvokeToolAction, call: ToolCall
    ) -> ProcessingOutcome:
        risk = self._gateway.assess(call)
        command = RequestApprovalCommand(
            run_id=context.run_id,
            category=risk.category,
            summary=risk.summary,
            reason=action.reason or "",
            requested_action=call.tool,
            requested_input=call.tool_input,
            authorization_fingerprint=self._fingerprint(context, call),
        )
        try:
            approval = self._request_tool_approval.execute(
                command,
                worker_id=context.worker_id,
                claim_token=context.claim_token,
                claim_generation=context.claim_generation,
            )
        except ClaimLost:
            return self._yield_now()
        return ProcessingOutcome.waiting_for_approval(approval.approval_id)

    # ------------------------------------------------------------- helpers

    def _fingerprint(self, context: ClaimContext, call: ToolCall) -> str:
        return compute_authorization_fingerprint(run_id=context.run_id, step_id=None, call=call)

    def _claim_holds(self, context: ClaimContext) -> bool:
        if context.is_lease_lost():
            return False
        return self._verify_claim.execute(
            context.run_id,
            context.worker_id,
            context.claim_token,
            context.claim_generation,
        )

    def _load_snapshot(
        self, context: ClaimContext, turn_notes: tuple[str, ...]
    ) -> RunSnapshot | None:
        """Short read-only transaction. Returns None when the run is not in
        a processable state (the fenced requeue path resolves the rest)."""
        with self._uow_factory() as uow:
            task = uow.tasks.get(context.task_id)
            run = uow.runs.get(context.run_id)
            if task is None or run is None or run.status is not RunStatus.RUNNING:
                return None
            events = uow.events.list_for_run(context.run_id)
            return RunSnapshot(
                task=task,
                run=run,
                steps=tuple(uow.steps.list_for_run(context.run_id)),
                approvals=tuple(uow.approvals.list_for_run(context.run_id)),
                invocations=tuple(uow.tool_invocations.list_for_run(context.run_id)),
                artifacts=tuple(uow.artifacts.list_for_run(context.run_id)),
                events=tuple(events[-_MAX_RECENT_EVENTS:]),
                previous_turns=turn_notes,
            )

    def _finish_blocker(self, snapshot: RunSnapshot) -> str | None:
        pending_steps = [
            step for step in snapshot.steps if step.status not in TERMINAL_RUN_STEP_STATUSES
        ]
        if pending_steps:
            return f"{len(pending_steps)} step(s) are not terminal"
        open_invocations = [
            invocation
            for invocation in snapshot.invocations
            if invocation.status not in TERMINAL_TOOL_INVOCATION_STATUSES
        ]
        if open_invocations:
            return f"{len(open_invocations)} tool invocation(s) are not terminal"
        return None

    def _yield_now(self) -> ProcessingOutcome:
        return ProcessingOutcome.yielded(self._clock.now())

    def _failed(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        cause: FailureCause = FailureCause.RUNTIME,
    ) -> ProcessingOutcome:
        bounded = message.strip()[:2000] or code
        return ProcessingOutcome.failed(
            Failure(code=code, message=bounded, retryable=retryable, cause=cause)
        )
