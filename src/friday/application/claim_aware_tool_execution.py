"""Claim-aware tool execution: the only path from a proposed tool action to
an actual side effect.

Transaction discipline (no Unit of Work is ever open while a tool runs):

    Txn A: verify claim -> authorize (fingerprint) -> consume approval ->
           create ToolInvocation (requested -> running) -> commit
                     |
                     v
           gateway.execute(...)          # outside any transaction
                     |
                     v
    Txn B: verify claim -> persist result/failure -> record artifacts ->
           commit

Claim loss at any checkpoint raises ClaimLost and persists nothing further;
a loss between Txn A and Txn B deliberately leaves the invocation RUNNING —
the side effect may or may not have happened, and §replay below governs what
a later claim may do about it.

Replay policy for protected (approval-required) actions: if the action's
fingerprint matches an already-consumed approval, the prior invocation's
durable outcome is authoritative — succeeded output is reused, a terminal
failure is surfaced, and a still-RUNNING invocation is ambiguous
(ToolExecutionAmbiguous): Friday never blindly re-executes a non-idempotent
action whose first execution may have completed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from friday.application.errors import (
    ClaimLost,
    EntityConflict,
    RunNotFound,
    ToolExecutionAmbiguous,
)
from friday.application.lifecycle_events import LifecycleEvents
from friday.application.ports import Clock, UnitOfWork, UnitOfWorkFactory
from friday.application.tool_authorization import (
    compute_authorization_fingerprint,
    find_authorizing_approval,
)
from friday.application.tool_gateway import (
    ArtifactCandidate,
    ToolCall,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolGateway,
    ToolRiskAssessment,
)
from friday.domain.approval import ApprovalRequest, ApprovalStatus
from friday.domain.artifact import Artifact
from friday.domain.event import RunEventType
from friday.domain.identifiers import (
    ArtifactId,
    RunId,
    RunStepId,
    ToolInvocationId,
)
from friday.domain.json_value import JsonValue
from friday.domain.run import RunStatus
from friday.domain.tool import ToolInvocation, ToolInvocationStatus


@dataclass(frozen=True, slots=True)
class ToolActionOutcome:
    """What happened to one proposed tool action."""

    kind: Literal["executed", "approval_required"]
    risk: ToolRiskAssessment
    fingerprint: str
    invocation_id: ToolInvocationId | None = None
    result: ToolExecutionResult | None = None
    replayed: bool = False

    @classmethod
    def approval_required(cls, risk: ToolRiskAssessment, fingerprint: str) -> ToolActionOutcome:
        return cls(kind="approval_required", risk=risk, fingerprint=fingerprint)

    @classmethod
    def executed(
        cls,
        risk: ToolRiskAssessment,
        fingerprint: str,
        invocation_id: ToolInvocationId,
        result: ToolExecutionResult,
        replayed: bool = False,
    ) -> ToolActionOutcome:
        return cls(
            kind="executed",
            risk=risk,
            fingerprint=fingerprint,
            invocation_id=invocation_id,
            result=result,
            replayed=replayed,
        )


class ExecuteToolAction(LifecycleEvents):
    """Authorize, durably record, execute, and persist one tool action under
    an exact worker claim."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock, gateway: ToolGateway) -> None:
        super().__init__(uow_factory, clock)
        self._gateway = gateway

    def execute(
        self,
        *,
        run_id: RunId,
        step_id: RunStepId | None,
        call: ToolCall,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
    ) -> ToolActionOutcome:
        # risk assessment is pure gateway policy — may raise ToolNotFound
        risk = self._gateway.assess(call)
        fingerprint = compute_authorization_fingerprint(run_id=run_id, step_id=step_id, call=call)

        # ---- Txn A: authorize and durably create the invocation ----------
        with self._uow_factory() as uow:
            now = self._clock.now()
            self._require_claim(uow, run_id, worker_id, claim_token, claim_generation, now)
            run = uow.runs.get(run_id)
            if run is None:
                raise RunNotFound(run_id)
            if run.status is not RunStatus.RUNNING:
                raise EntityConflict("run is not running")

            approval_id = None
            if risk.approval_required:
                approvals = uow.approvals.list_for_run(run_id)
                replay = self._find_replay(uow, approvals, fingerprint)
                if replay is not None:
                    uow.commit()
                    return ToolActionOutcome.executed(
                        risk, fingerprint, replay[0], replay[1], replayed=True
                    )
                authorizing = find_authorizing_approval(approvals, fingerprint=fingerprint)
                if authorizing is None:
                    uow.commit()
                    return ToolActionOutcome.approval_required(risk, fingerprint)
                authorizing.consume(now)
                uow.approvals.save(authorizing)
                approval_id = authorizing.id

            invocation = ToolInvocation.new(
                id=ToolInvocationId.new(),
                run_id=run_id,
                tool_name=call.tool,
                requested_input=call.tool_input,
                requested_at=now,
                step_id=step_id,
                approval_request_id=approval_id,
            )
            uow.tool_invocations.add(invocation)
            invocation.start(now)
            uow.tool_invocations.save(invocation)
            self.append_run_events(
                uow,
                run,
                now,
                [
                    (
                        RunEventType.TOOL_INVOCATION_REQUESTED,
                        {
                            "tool_invocation_id": str(invocation.id),
                            "tool_name": invocation.tool_name,
                        },
                        step_id,
                    ),
                    (
                        RunEventType.TOOL_INVOCATION_STARTED,
                        {"tool_invocation_id": str(invocation.id)},
                        step_id,
                    ),
                ],
            )
            uow.commit()
            invocation_id = invocation.id

        # ---- execute outside any transaction ------------------------------
        result = self._gateway.execute(
            ToolExecutionRequest(
                invocation_id=invocation_id, run_id=run_id, step_id=step_id, call=call
            )
        )

        # ---- Txn B: persist the outcome under a still-valid claim ---------
        with self._uow_factory() as uow:
            now = self._clock.now()
            self._require_claim(uow, run_id, worker_id, claim_token, claim_generation, now)
            run = uow.runs.get(run_id)
            if run is None:
                raise RunNotFound(run_id)
            persisted = uow.tool_invocations.get(invocation_id)
            if persisted is None or persisted.status is not ToolInvocationStatus.RUNNING:
                raise EntityConflict("tool invocation is no longer running")

            events: list[tuple[RunEventType, JsonValue, RunStepId | None]] = []
            if result.status == "succeeded":
                persisted.succeed(now, result.output)
                events.append(
                    (
                        RunEventType.TOOL_INVOCATION_SUCCEEDED,
                        {"tool_invocation_id": str(invocation_id)},
                        step_id,
                    )
                )
            else:
                assert result.failure is not None  # ToolExecutionResult invariant
                persisted.fail(now, result.failure)
                events.append(
                    (
                        RunEventType.TOOL_INVOCATION_FAILED,
                        {
                            "tool_invocation_id": str(invocation_id),
                            "failure_code": result.failure.code,
                        },
                        step_id,
                    )
                )
            uow.tool_invocations.save(persisted)
            for candidate in result.artifacts:
                artifact = self._artifact_from(candidate, run_id, step_id, now)
                uow.artifacts.add(artifact)
                events.append(
                    (
                        RunEventType.ARTIFACT_CREATED,
                        {
                            "artifact_id": str(artifact.id),
                            "kind": artifact.kind.value,
                            "location": artifact.location,
                        },
                        step_id,
                    )
                )
            self.append_run_events(uow, run, now, list(events))
            uow.commit()

        return ToolActionOutcome.executed(risk, fingerprint, invocation_id, result)

    def _require_claim(
        self,
        uow: UnitOfWork,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
    ) -> None:
        if not uow.work_queue.is_claim_active(
            run_id, worker_id, claim_token, claim_generation, now
        ):
            raise ClaimLost("claim is no longer active; refusing tool execution step")

    def _find_replay(
        self,
        uow: UnitOfWork,
        approvals: list[ApprovalRequest],
        fingerprint: str,
    ) -> tuple[ToolInvocationId, ToolExecutionResult] | None:
        """Durable replay detection for protected actions (see module doc)."""
        consumed = [
            approval
            for approval in approvals
            if approval.status is ApprovalStatus.APPROVED
            and approval.authorization_fingerprint == fingerprint
            and approval.is_consumed
        ]
        if not consumed:
            return None
        for approval in consumed:
            for invocation in uow.tool_invocations.list_for_run(approval.run_id):
                if invocation.approval_request_id != approval.id:
                    continue
                if invocation.status is ToolInvocationStatus.SUCCEEDED:
                    return invocation.id, ToolExecutionResult.succeeded(invocation.output)
                if invocation.status is ToolInvocationStatus.FAILED:
                    assert invocation.failure is not None
                    return invocation.id, ToolExecutionResult.failed(invocation.failure)
                raise ToolExecutionAmbiguous(
                    "a prior protected execution for this exact action is not terminal; "
                    "its side effect may have completed — refusing automatic replay"
                )
        return None

    def _artifact_from(
        self,
        candidate: ArtifactCandidate,
        run_id: RunId,
        step_id: RunStepId | None,
        now: datetime,
    ) -> Artifact:
        return Artifact(
            id=ArtifactId.new(),
            run_id=run_id,
            step_id=step_id,
            kind=candidate.kind,
            name=candidate.name,
            media_type=candidate.media_type,
            location=candidate.location,
            created_at=now,
            size=candidate.size,
            checksum=candidate.checksum,
        )
