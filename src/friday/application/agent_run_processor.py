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

import hashlib
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from friday.application.agent_registry import ResolveRunAgent
from friday.application.brain_runtime import BrainRequest, BrainResponse, BrainRuntime
from friday.application.brain_runtime_registry import BrainRuntimeRegistry
from friday.application.claim_aware_tool_execution import ExecuteToolAction
from friday.application.commands import RequestApprovalCommand
from friday.application.conversation_context import ConversationContextAssembler
from friday.application.delegation import DispatchDelegation
from friday.application.delegation_result_safety import AuthorityValue, project_delegated_result
from friday.application.errors import (
    AgentIntegrityFailed,
    ApplicationError,
    BrainProtocolError,
    BrainResponseInvalid,
    BrainTimeout,
    BrainUnavailable,
    ClaimLost,
    InvalidBrainRuntimeConfig,
    SkillIntegrityFailed,
    ToolExecutionAmbiguous,
    ToolInputInvalid,
    ToolNotFound,
    UnknownBrainRuntimeKind,
)
from friday.application.lifecycle_events import LifecycleEvents
from friday.application.memory.models import (
    IndexState,
    MemoryContext,
    MemoryQuery,
    MemoryRetrievalItem,
    MemoryRetrievalRecord,
    RetrievalMode,
)
from friday.application.memory.ports import MemoryRetrieverPort
from friday.application.memory.query_builder import (
    MemoryQueryBuilder,
)
from friday.application.memory.query_builder import (
    RunSnapshot as MemoryRunSnapshot,
)
from friday.application.ports import Clock, UnitOfWork, UnitOfWorkFactory
from friday.application.run_processor import ClaimContext, ProcessingOutcome
from friday.application.runtime_actions import (
    BrainAction,
    DelegateAction,
    FailAction,
    FinishAction,
    InvokeToolAction,
    YieldAction,
)
from friday.application.runtime_context import (
    MIN_CONTEXT_CHARS,
    AgentContextTooLarge,
    DelegatedContextTooLarge,
    DelegationTarget,
    DelegationView,
    RunSnapshot,
    SkillContextTooLarge,
    build_runtime_context,
)
from friday.application.skill_registry import ResolveRunSkills
from friday.application.tool_authorization import (
    RequestToolApproval,
    compute_authorization_fingerprint,
)
from friday.application.tool_gateway import ToolCall, ToolGateway, ToolRiskAssessment
from friday.application.worker_coordination import VerifyRunClaim
from friday.application.workflow_context import build_workflow_node_context
from friday.domain.delegation import (
    MAX_DELEGATION_DEPTH,
    MAX_DELEGATIONS_PER_RUN,
    MAX_DELEGATIONS_PER_TREE,
    DelegationRequest,
)
from friday.domain.errors import DomainValidationError
from friday.domain.event import RunEventType
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import DelegationRequestId, RunId, RunStepId
from friday.domain.json_value import JsonValue
from friday.domain.run import RunStatus
from friday.domain.step import TERMINAL_RUN_STEP_STATUSES
from friday.domain.tool import TERMINAL_TOOL_INVOCATION_STATUSES

_MAX_TURN_NOTE_CHARS = 500
_MAX_RECENT_EVENTS = 50
_MEMORY_WRITE_TOOLS = frozenset({"memory.create_note", "memory.append_managed_note"})


@dataclass(frozen=True, slots=True)
class RuntimeLimits:
    max_turns_per_claim: int
    max_tool_calls_per_claim: int
    max_context_chars: int
    max_response_bytes: int
    max_yield_seconds: int
    max_processing_seconds: float = 600.0
    max_skill_context_chars: int = 24_000
    max_agent_context_chars: int = 24_000
    max_delegations_per_run: int = MAX_DELEGATIONS_PER_RUN
    max_delegations_per_tree: int = MAX_DELEGATIONS_PER_TREE
    max_delegation_targets: int = 32
    max_delegation_depth: int = MAX_DELEGATION_DEPTH

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
        if self.max_processing_seconds <= 0:
            raise ValueError("max_processing_seconds must be positive")
        if not 0 < self.max_skill_context_chars < self.max_context_chars:
            raise ValueError("max_skill_context_chars must be positive and below max_context_chars")
        if not 0 < self.max_agent_context_chars <= self.max_context_chars:
            raise ValueError(
                "max_agent_context_chars must be positive and at most max_context_chars"
            )
        if self.max_delegations_per_run < 1:
            raise ValueError("max_delegations_per_run must be positive")
        if self.max_delegations_per_tree < 1:
            raise ValueError("max_delegations_per_tree must be positive")
        if self.max_delegation_targets < 1:
            raise ValueError("max_delegation_targets must be positive")
        if self.max_delegation_depth < 1:
            raise ValueError("max_delegation_depth must be >= 1")


class AgentRunProcessor:
    """Satisfies Phase 10's RunProcessor protocol with a real agent loop."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        brain: BrainRuntime,
        runtime_registry: BrainRuntimeRegistry,
        gateway: ToolGateway,
        verify_claim: VerifyRunClaim,
        request_tool_approval: RequestToolApproval,
        execute_tool_action: ExecuteToolAction,
        limits: RuntimeLimits,
        dispatch_delegation: DispatchDelegation | None = None,
        memory_retriever: MemoryRetrieverPort | None = None,
        memory_query_builder: MemoryQueryBuilder | None = None,
        conversation_context: ConversationContextAssembler | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._brain = brain
        self._gateway = gateway
        self._runtime_registry = runtime_registry
        self._verify_claim = verify_claim
        self._request_tool_approval = request_tool_approval
        self._execute_tool_action = execute_tool_action
        self._dispatch_delegation = dispatch_delegation or DispatchDelegation(
            uow_factory,
            clock,
            runtime_registry,
            max_delegations_per_run=limits.max_delegations_per_run,
            max_delegations_per_tree=limits.max_delegations_per_tree,
            max_delegation_depth=limits.max_delegation_depth,
        )
        self._limits = limits
        self._memory_retriever = memory_retriever
        self._memory_query_builder = memory_query_builder or MemoryQueryBuilder()
        self._conversation_context = conversation_context
        self._monotonic = monotonic
        self._resolve_run_skills = ResolveRunSkills(uow_factory, clock)
        self._resolve_run_agent = ResolveRunAgent(uow_factory, clock, runtime_registry)

    # ------------------------------------------------------------------ API

    def process(self, context: ClaimContext) -> ProcessingOutcome:
        turn_notes: list[str] = []
        tool_calls = 0
        memory: MemoryContext | None = None
        memory_refresh_available = True
        memory_refresh_needed = self._memory_retriever is not None
        memory_retrieval_is_refresh = False
        previous_objective: tuple[str, str] | None = None
        deadline = self._monotonic() + self._limits.max_processing_seconds

        for turn in range(1, self._limits.max_turns_per_claim + 1):
            if self._monotonic() >= deadline:
                return self._yield_now()
            if not self._claim_holds(context):
                return self._yield_now()

            try:
                self._resolve_run_skills.execute(
                    context.run_id,
                    context.worker_id,
                    context.claim_token,
                    context.claim_generation,
                )
            except ClaimLost:
                # A stale worker must yield its claim; it is not a Run-level
                # Skill failure and must never terminalize the Run.
                return self._yield_now()
            except SkillIntegrityFailed:
                return self._failed(
                    "skill_integrity_failed",
                    "skill integrity verification failed",
                    retryable=False,
                )
            except ApplicationError:
                return self._failed(
                    "skill_resolution_failed",
                    "skill resolution failed",
                    retryable=False,
                )

            try:
                self._resolve_run_agent.execute(
                    context.run_id,
                    context.worker_id,
                    context.claim_token,
                    context.claim_generation,
                )
            except ClaimLost:
                # A stale worker must yield its claim; it is not a Run-level
                # Agent failure and must never terminalize the Run.
                return self._yield_now()
            except AgentIntegrityFailed:
                return self._failed(
                    "agent_integrity_failed",
                    "agent integrity verification failed",
                    retryable=False,
                )
            except ApplicationError:
                return self._failed(
                    "agent_resolution_failed",
                    "agent resolution failed",
                    retryable=False,
                )

            try:
                snapshot = self._load_snapshot(context, tuple(turn_notes))
            except SkillIntegrityFailed:
                return self._failed(
                    "skill_integrity_failed",
                    "skill integrity verification failed",
                    retryable=False,
                )
            except AgentIntegrityFailed:
                return self._failed(
                    "agent_integrity_failed",
                    "agent integrity verification failed",
                    retryable=False,
                )
            if snapshot is None:
                return self._yield_now()
            conversation = (
                self._conversation_context.assemble(context.run_id)
                if self._conversation_context
                else None
            )

            objective = (snapshot.task.title, snapshot.task.description)
            if previous_objective is not None and objective != previous_objective:
                memory_refresh_needed = memory_refresh_available
                memory_retrieval_is_refresh = memory_refresh_needed
            previous_objective = objective
            if memory_refresh_needed:
                memory = self._retrieve_memory(context, snapshot, turn)
                memory_refresh_needed = False
                if memory_retrieval_is_refresh:
                    memory_refresh_available = False
                memory_retrieval_is_refresh = False
                if memory is None:
                    return self._yield_now()

            try:
                document = build_runtime_context(
                    snapshot,
                    tool_manifest=self._gateway.list_tools(),
                    attempt_number=context.attempt_number,
                    turn_number=turn,
                    max_chars=self._limits.max_context_chars,
                    max_skill_context_chars=self._limits.max_skill_context_chars,
                    max_agent_context_chars=self._limits.max_agent_context_chars,
                    memory_context=memory,
                    workflow_context=snapshot.workflow_context,
                    conversation_context=conversation,
                )
            except SkillContextTooLarge:
                return self._failed(
                    "skill_context_too_large",
                    "frozen skill context exceeds budget",
                    retryable=False,
                )
            except AgentContextTooLarge:
                return self._failed(
                    "agent_context_too_large",
                    "frozen Agent context exceeds budget",
                    retryable=False,
                )
            except DelegatedContextTooLarge:
                return self._failed(
                    "delegated_context_too_large",
                    "delegated input exceeds the runtime context budget",
                    retryable=False,
                )
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return self._yield_now()
            request = BrainRequest(
                run_id=context.run_id,
                task_id=context.task_id,
                turn_number=turn,
                attempt_number=context.attempt_number,
                context=document,
                tool_manifest=self._gateway.list_tools(),
                max_response_bytes=self._limits.max_response_bytes,
                timeout_seconds=remaining,
            )

            try:
                if self._monotonic() >= deadline:
                    return self._yield_now()
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

            if self._monotonic() >= deadline:
                return self._yield_now()
            outcome, note, tool_call_used, successful_memory_write = self._dispatch(
                context, response, snapshot, deadline
            )
            if outcome is not None:
                return outcome
            if note is not None:
                turn_notes.append(note[:_MAX_TURN_NOTE_CHARS])
            if tool_call_used:
                tool_calls += 1
                if successful_memory_write:
                    memory_refresh_needed = memory_refresh_available
                    memory_retrieval_is_refresh = memory_refresh_needed
                if tool_calls >= self._limits.max_tool_calls_per_claim:
                    return self._yield_now()  # tool budget: continue under a fresh claim

        return self._yield_now()  # turn budget: continue under a fresh claim

    # ------------------------------------------------------------ dispatch

    def _dispatch(
        self,
        context: ClaimContext,
        response: BrainResponse,
        snapshot: RunSnapshot,
        deadline: float,
    ) -> tuple[ProcessingOutcome | None, str | None, bool, bool]:
        """Returns (final outcome | None to continue, turn note, tool used,
        successful memory write)."""
        action: BrainAction = response.action
        if isinstance(action, FinishAction):
            blocker = self._finish_blocker(context.run_id, snapshot)
            if blocker is not None:
                return None, f"finish rejected: {blocker}", False, False
            if not self._claim_holds(context):
                return self._yield_now(), None, False, False
            return ProcessingOutcome.succeeded(action.summary, action.details), None, False, False

        if isinstance(action, FailAction):
            return (
                self._failed(
                    "agent_reported_failure",
                    action.reason,
                    retryable=False,
                ),
                None,
                False,
                False,
            )

        if isinstance(action, YieldAction):
            delay = min(action.delay_seconds or 0, self._limits.max_yield_seconds)
            available_at = self._clock.now() + timedelta(seconds=delay)
            return ProcessingOutcome.yielded(available_at), None, False, False

        if isinstance(action, DelegateAction):
            try:
                request = self._dispatch_delegation.execute(
                    parent_run_id=context.run_id,
                    worker_id=context.worker_id,
                    claim_token=context.claim_token,
                    claim_generation=context.claim_generation,
                    target_agent_key=action.target_agent_key,
                    objective=action.objective,
                    input_payload=action.input_payload,
                    expected_output_contract=action.expected_output_contract,
                )
            except ClaimLost:
                return self._yield_now(), None, False, False
            except ApplicationError as exc:
                message = str(exc)
                code = (
                    message
                    if message
                    in {
                        "delegation_budget_exhausted",
                        "delegation_depth_exhausted",
                    }
                    else "delegation_dispatch_failed"
                )
                return (
                    self._failed(code, message, retryable=False),
                    None,
                    False,
                    False,
                )
            return (
                ProcessingOutcome.waiting_for_delegation(request.id),
                f"delegated to {action.target_agent_key}",
                False,
                False,
            )

        return self._dispatch_tool(context, action, deadline)

    def _dispatch_tool(
        self, context: ClaimContext, action: InvokeToolAction, deadline: float
    ) -> tuple[ProcessingOutcome | None, str | None, bool, bool]:
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            return self._yield_now(), None, False, False
        try:
            call = ToolCall(tool=action.tool, tool_input=action.tool_input)
        except ToolInputInvalid as exc:
            return None, f"invalid tool input for {action.tool}: {exc}", False, False

        try:
            result = self._execute_tool_action.execute(
                run_id=context.run_id,
                step_id=None,
                call=call,
                worker_id=context.worker_id,
                claim_token=context.claim_token,
                claim_generation=context.claim_generation,
                cancellation_requested=context.is_lease_lost,
                timeout_seconds=remaining,
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
                False,
            )
        except ClaimLost:
            return self._yield_now(), None, False, False

        if result.kind == "approval_required":
            return self._request_approval(context, action, call), None, False, False

        status = result.result.status if result.result is not None else "unknown"
        note = f"invoked {call.tool} -> {status}"
        if result.replayed:
            note += " (replayed durable result)"
        return None, note, True, _is_successful_memory_write(call.tool, status)

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
            authorization_fingerprint=self._fingerprint(context, call, risk),
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

    def _fingerprint(self, context: ClaimContext, call: ToolCall, risk: ToolRiskAssessment) -> str:
        return compute_authorization_fingerprint(
            run_id=context.run_id,
            step_id=None,
            call=call,
            authorization_scope=risk.authorization_scope,
        )

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
            events = _bounded_read(uow.events, context.run_id, _MAX_RECENT_EVENTS)
            skills = []
            for binding in uow.run_skill_bindings.list_for_run(context.run_id):
                skill = uow.skills.get(binding.skill_id)
                revision = uow.skill_revisions.get(binding.revision_id)
                if skill is None or revision is None or revision.skill_id != binding.skill_id:
                    return None
                if hashlib.sha256(revision.instructions.encode("utf-8")).hexdigest() != (
                    revision.content_sha256
                ):
                    raise SkillIntegrityFailed()
                skills.append((binding, skill, revision))
            agent = None
            resolution = uow.run_agent_resolutions.get(context.run_id)
            if resolution is not None:
                identity = uow.agents.get(resolution.agent_id)
                try:
                    agent_revision = uow.agent_revisions.get(resolution.revision_id)
                except DomainValidationError as exc:
                    raise AgentIntegrityFailed() from exc
                if (
                    identity is None
                    or agent_revision is None
                    or resolution.agent_id != identity.id
                    or resolution.revision_id != agent_revision.id
                    or agent_revision.agent_id != identity.id
                ):
                    raise AgentIntegrityFailed()
                expected_sha = hashlib.sha256(
                    json.dumps(
                        {
                            "instructions": agent_revision.instructions,
                            "runtime_kind": agent_revision.runtime_kind,
                            "runtime_config": agent_revision.runtime_config,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                if agent_revision.content_sha256 != expected_sha:
                    raise AgentIntegrityFailed()
                try:
                    self._runtime_registry.validate_runtime_config(
                        agent_revision.runtime_kind, agent_revision.runtime_config
                    )
                except (UnknownBrainRuntimeKind, InvalidBrainRuntimeConfig) as exc:
                    raise AgentIntegrityFailed() from exc
                agent = (resolution, identity, agent_revision)

            targets: list[DelegationTarget] = []
            if agent is not None:
                for identity in sorted(
                    uow.agents.list(100_000), key=lambda value: (value.key, str(value.id))
                ):
                    if (
                        identity.status.value != "active"
                        or identity.active_revision_id is None
                        or len(targets) >= self._limits.max_delegation_targets
                    ):
                        continue
                    try:
                        target_revision = uow.agent_revisions.get(identity.active_revision_id)
                    except DomainValidationError as exc:
                        raise AgentIntegrityFailed() from exc
                    if target_revision is None or target_revision.agent_id != identity.id:
                        continue
                    targets.append(
                        DelegationTarget(identity.key, identity.display_name, identity.description)
                    )

            delegation_views: list[DelegationView] = []
            for request in uow.delegation_requests.list_for_run(context.run_id):
                target = uow.agents.get(request.target_agent_id)
                target_key = target.key if target is not None else str(request.target_agent_id)
                child_execution_id = None
                summary = None
                details: JsonValue = None
                authority_values: tuple[AuthorityValue, ...] = ()
                if request.child_run_id is not None:
                    child = uow.runs.get(request.child_run_id)
                    if child is not None:
                        child_execution_id = str(child.execution_id)
                        if request.status.value == "succeeded":
                            authority_values = _delegated_lineage_authority_values(
                                uow, child.execution_id, request
                            )
                            successful = [
                                candidate
                                for candidate in uow.runs.list_for_execution(child.execution_id)
                                if candidate.status is RunStatus.SUCCEEDED
                            ]
                            for candidate in reversed(successful):
                                finished = uow.events.latest_of_type_for_run(
                                    candidate.id, RunEventType.AGENT_FINISHED
                                )
                                if finished is not None and isinstance(finished.payload, dict):
                                    raw_summary = finished.payload.get("summary")
                                    raw_details = finished.payload.get("details")
                                    summary, details = project_delegated_result(
                                        raw_summary if isinstance(raw_summary, str) else None,
                                        raw_details,
                                        authority_values=authority_values,
                                    )
                                    break
                delegation_views.append(
                    DelegationView(
                        request=request,
                        target_key=target_key,
                        child_execution_id=child_execution_id,
                        summary=summary,
                        details=details,
                        authority_values=authority_values,
                    )
                )
            incoming = uow.delegation_requests.get_for_child_execution(run.execution_id)
            return RunSnapshot(
                task=task,
                run=run,
                steps=tuple(uow.steps.list_for_run(context.run_id)),
                approvals=tuple(_bounded_read(uow.approvals, context.run_id, _MAX_RECENT_EVENTS)),
                invocations=tuple(
                    _bounded_read(uow.tool_invocations, context.run_id, _MAX_RECENT_EVENTS)
                ),
                artifacts=tuple(_bounded_read(uow.artifacts, context.run_id, _MAX_RECENT_EVENTS)),
                events=tuple(events),
                previous_turns=turn_notes,
                skills=tuple(skills),
                agent=agent,
                delegation_targets=tuple(targets),
                delegations=tuple(delegation_views),
                incoming_delegation=incoming,
                workflow_context=_workflow_context_for_run(uow, run.execution_id),
            )

    def _retrieve_memory(
        self, context: ClaimContext, snapshot: RunSnapshot, turn: int
    ) -> MemoryContext | None:
        """Retrieve outside a transaction and discard results on claim loss."""
        query = self._memory_query_builder.build(
            MemoryRunSnapshot(
                task_title=snapshot.task.title,
                task_description=snapshot.task.description,
                objective=snapshot.task.title,
                step_names=tuple(step.name for step in snapshot.steps),
                failure_codes=tuple(
                    step.failure.code for step in snapshot.steps if step.failure is not None
                ),
                tool_names=tuple(invocation.tool_name for invocation in snapshot.invocations),
            )
        )
        if query is None:
            return MemoryContext(RetrievalMode.DISABLED, (), (), None, IndexState.DISABLED, 0)
        try:
            assert self._memory_retriever is not None
            memory = self._memory_retriever.retrieve(query=query)
        except Exception:
            memory = MemoryContext(
                RetrievalMode.UNAVAILABLE,
                (),
                (),
                "memory retrieval is unavailable",
                IndexState.DISABLED,
                0,
            )
        if not self._claim_holds(context):
            return None
        try:
            self._record_memory_events(context, turn, query, memory)
        except ClaimLost:
            # Retrieval itself is deliberately outside a transaction.  Its
            # audit is not: a lost lease must discard both the context and
            # every durable trace of this worker's retrieval.
            return None
        return memory

    def _record_memory_events(
        self, context: ClaimContext, turn: int, query: MemoryQuery, memory: MemoryContext
    ) -> None:
        specs: list[tuple[RunEventType, JsonValue, RunStepId | None]] = [
            (
                RunEventType.MEMORY_CONTEXT_ATTACHED,
                {"mode": memory.mode.value, "excerpt_count": len(memory.excerpts)},
                None,
            )
        ]
        if memory.degraded_reason is not None:
            specs.append(
                (
                    RunEventType.MEMORY_RETRIEVAL_DEGRADED,
                    {"mode": memory.mode.value, "reason": memory.degraded_reason},
                    None,
                )
            )
        with self._uow_factory() as uow:
            if not uow.work_queue.is_claim_active(
                context.run_id,
                context.worker_id,
                context.claim_token,
                context.claim_generation,
                self._clock.now(),
            ):
                raise ClaimLost("claim is no longer active; refusing memory retrieval audit")
            run = uow.runs.get(context.run_id)
            if run is not None:
                LifecycleEvents.append_run_events(uow, run, self._clock.now(), specs)
                uow.memory_retrieval_records.add(
                    _build_memory_retrieval_record(context, turn, query, memory, self._clock.now())
                )
            uow.commit()

    def _finish_blocker(self, run_id: RunId, snapshot: RunSnapshot | None = None) -> str | None:
        with self._uow_factory() as uow:
            has_steps = getattr(uow.steps, "has_non_terminal_for_run", None)
            if (has_steps is not None and has_steps(run_id)) or (
                has_steps is None
                and snapshot is not None
                and any(step.status not in TERMINAL_RUN_STEP_STATUSES for step in snapshot.steps)
            ):
                if has_steps is None and snapshot is not None:
                    count = sum(
                        step.status not in TERMINAL_RUN_STEP_STATUSES for step in snapshot.steps
                    )
                    return f"{count} step(s) are not terminal"
                return "one or more steps are not terminal"
            has_tools = getattr(uow.tool_invocations, "has_non_terminal_for_run", None)
            if (has_tools is not None and has_tools(run_id)) or (
                has_tools is None
                and snapshot is not None
                and any(
                    inv.status not in TERMINAL_TOOL_INVOCATION_STATUSES
                    for inv in snapshot.invocations
                )
            ):
                if has_tools is None and snapshot is not None:
                    count = sum(
                        inv.status not in TERMINAL_TOOL_INVOCATION_STATUSES
                        for inv in snapshot.invocations
                    )
                    return f"{count} tool invocation(s) are not terminal"
                return "one or more tool invocations are not terminal"
            has_approvals = getattr(uow.approvals, "has_pending_for_run", None)
            if (has_approvals is not None and has_approvals(run_id)) or (
                has_approvals is None
                and snapshot is not None
                and any(approval.status.value == "pending" for approval in snapshot.approvals)
            ):
                return "one or more approvals are pending"
            if uow.delegation_requests.has_dispatched_for_run(run_id):
                return "one or more delegations are active"
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


def _workflow_context_for_run(uow: object, execution_id: object) -> str | None:
    nodes_repo = getattr(uow, "workflow_node_executions", None)
    if (
        nodes_repo is None
        or getattr(uow, "workflow_executions", None) is None
        or getattr(uow, "workflow_revisions", None) is None
        or getattr(uow, "workflows", None) is None
    ):
        return None
    node_execution = nodes_repo.get_by_child_execution_id(execution_id)
    if node_execution is None:
        return None
    return build_workflow_node_context(cast(UnitOfWork, uow), node_execution)


def _bounded_read(repository: object, run_id: object, limit: int) -> Any:
    recent = getattr(repository, "list_recent_for_run", None)
    if recent is not None:
        return recent(run_id, limit)
    # Compatibility for in-memory ports used by older callers; production
    # repositories implement the bounded query above.
    return list(getattr(repository, "list_for_run")(run_id))[-limit:]  # noqa: B009


def _delegated_lineage_authority_values(
    uow: UnitOfWork, child_execution_id: RunId, request: DelegationRequest
) -> tuple[AuthorityValue, ...]:
    """Collect the actual durable authority values of one delegated child
    execution lineage.  This is the fail-closed input for the parent-facing
    result projection: approval ids and authorization fingerprints, tool
    invocation ids, and Friday-owned delegation identifiers and fingerprints
    across the whole subtree (all retry attempts, all nested hops).  Literal
    occurrences of these values must never travel upward, regardless of which
    field name or free text carries them."""

    values: set[AuthorityValue] = set()

    def remember(value: object) -> None:
        if value is None:
            return
        if isinstance(value, bool):
            return
        if isinstance(value, int):
            if value > 0:
                values.add(value)
            return
        text = str(value)
        if text:
            values.add(text)

    def remember_json_literals(value: JsonValue) -> None:
        if isinstance(value, dict):
            for child in value.values():
                remember_json_literals(child)
        elif isinstance(value, list):
            for child in value:
                remember_json_literals(child)
        elif isinstance(value, (str, int)) and not isinstance(value, bool):
            remember(value)

    for request_identifier in (
        request.id,
        request.authorization_fingerprint,
        request.parent_run_step_id,
        request.root_delegation_id,
        request.child_task_id,
        request.child_run_id,
    ):
        remember(request_identifier)
    pending_executions = [child_execution_id]
    seen_executions: set[RunId] = set()
    seen_requests: set[DelegationRequestId] = set()
    seen_requests.add(request.id)
    while pending_executions:
        execution_id = pending_executions.pop()
        if execution_id in seen_executions:
            continue
        seen_executions.add(execution_id)
        remember(execution_id)
        for run in uow.runs.list_for_execution(execution_id):
            for run_identifier in (
                run.id,
                run.task_id,
                run.execution_id,
                run.approval_request_id,
                run.delegation_request_id,
                run.workflow_execution_id,
            ):
                remember(run_identifier)

            resolution = uow.run_agent_resolutions.get(run.id)
            if resolution is not None:
                for resolution_identifier in (
                    resolution.id,
                    resolution.run_id,
                    resolution.agent_id,
                    resolution.revision_id,
                ):
                    remember(resolution_identifier)
                revision = uow.agent_revisions.get(resolution.revision_id)
                if revision is not None:
                    for revision_value in (
                        revision.id,
                        revision.agent_id,
                        revision.content_sha256,
                        revision.runtime_kind,
                    ):
                        remember(revision_value)
                    remember_json_literals(revision.runtime_config)

            work_item = uow.work_queue.get(run.id)
            if work_item is not None:
                remember(work_item.claim_token)
                remember(work_item.claim_generation)

            for step in uow.steps.list_for_run(run.id):
                for step_identifier in (step.id, step.run_id, step.approval_request_id):
                    remember(step_identifier)

            for approval in uow.approvals.list_for_run(run.id):
                for approval_identifier in (
                    approval.id,
                    approval.run_id,
                    approval.step_id,
                    approval.subject_id,
                    approval.authorization_fingerprint,
                ):
                    remember(approval_identifier)

            for invocation in uow.tool_invocations.list_for_run(run.id):
                for invocation_identifier in (
                    invocation.id,
                    invocation.run_id,
                    invocation.step_id,
                    invocation.approval_request_id,
                ):
                    remember(invocation_identifier)
                if invocation.provenance is not None:
                    for handle in (
                        invocation.provenance.target,
                        invocation.provenance.remote_name,
                        invocation.provenance.binding_fingerprint,
                    ):
                        remember(handle)

            for nested in uow.delegation_requests.list_for_run(run.id):
                if nested.id in seen_requests:
                    continue
                seen_requests.add(nested.id)
                for identifier in (
                    nested.id,
                    nested.authorization_fingerprint,
                    nested.parent_run_step_id,
                    nested.root_delegation_id,
                    nested.child_task_id,
                    nested.child_run_id,
                ):
                    remember(identifier)
                if nested.child_run_id is not None:
                    nested_child = uow.runs.get(nested.child_run_id)
                    if nested_child is not None:
                        pending_executions.append(nested_child.execution_id)
    return tuple(sorted(values, key=lambda value: (isinstance(value, int), str(value))))


def _build_memory_retrieval_record(
    context: ClaimContext, turn: int, query: MemoryQuery, memory: MemoryContext, now: datetime
) -> MemoryRetrievalRecord:
    """Durable audit of one retrieval: bounded metadata and per-excerpt
    provenance only -- no excerpt bodies or query text are persisted."""
    items = tuple(
        MemoryRetrievalItem(
            path=provenance.path,
            heading=provenance.heading,
            start_line=provenance.start_line,
            end_line=provenance.end_line,
            content_hash=provenance.content_hash,
            rank=provenance.rank,
            methods=provenance.methods,
            truncated=provenance.truncated,
        )
        for provenance in memory.provenance
    )
    first = memory.provenance[0] if memory.provenance else None
    return MemoryRetrievalRecord(
        id=str(uuid.uuid4()),
        run_id=context.run_id,
        turn_number=turn,
        query_hash=query.query_hash,
        source_snapshot_id=first.source_snapshot_id if first is not None else None,
        index_snapshot_id=memory.index_snapshot_id,
        created_at=now,
        candidate_count=len(memory.excerpts),
        selected_count=len(memory.excerpts),
        items=items,
    )


def _is_successful_memory_write(tool: str, status: str) -> bool:
    """A refresh is only warranted for a write that actually happened --
    memory.search/read_note never mutate anything, and a failed
    create/append leaves nothing new for the next retrieval to see."""
    return tool in _MEMORY_WRITE_TOOLS and status == "succeeded"
