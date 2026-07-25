"""Exact-action approval binding.

An approval authorizes exactly one tool action: the SHA-256 fingerprint
below covers the fingerprint version, Run, optional RunStep, tool name, and
the canonical JSON form of the tool input. Any change to any of these —
another run, another step, another tool, a reordered-but-different input —
produces a different fingerprint, and the approval no longer matches.

`RunStatus.RUNNING` never implies authorization; only an APPROVED,
never-consumed approval whose fingerprint equals the proposed action's
authorizes execution. Consumption (ApprovalRequest.consume) makes the grant
one-shot for non-idempotent tools.

RequestToolApproval is the claim-aware variant of Phase 8's RequestApproval:
same coordination semantics (park the run, remove the work item, emit
events), but every durable effect is fenced by the worker's exact claim —
a stale worker can neither create the approval nor remove the fresh work
item."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from friday.application.approval_workflow import approval_result
from friday.application.commands import RequestApprovalCommand
from friday.application.errors import (
    ClaimLost,
    EntityConflict,
    RunNotFound,
    RunStepNotFound,
)
from friday.application.lifecycle_events import LifecycleEvents
from friday.application.results import ApprovalRequestResult
from friday.application.tool_gateway import ToolCall
from friday.domain.approval import ApprovalRequest, ApprovalStatus
from friday.domain.event import RunEventType
from friday.domain.identifiers import ApprovalRequestId, RunId, RunStepId
from friday.domain.run import RunStatus
from friday.domain.step import RunStep, RunStepStatus

FINGERPRINT_VERSION = 1


def compute_authorization_fingerprint(
    *, run_id: RunId, step_id: RunStepId | None, call: ToolCall
) -> str:
    """Deterministic SHA-256 binding of one exact tool action to one Run.

    Canonical JSON (sorted keys, no whitespace) makes the fingerprint
    independent of input key order but sensitive to any value change. Uses
    hashlib — never Python's process-randomized hash()."""
    canonical_input = json.dumps(call.tool_input, sort_keys=True, separators=(",", ":"))
    material = "\n".join(
        [
            str(FINGERPRINT_VERSION),
            str(run_id),
            str(step_id) if step_id is not None else "",
            call.tool,
            canonical_input,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def find_authorizing_approval(
    approvals: Sequence[ApprovalRequest], *, fingerprint: str
) -> ApprovalRequest | None:
    """The only path to an authorized protected execution: APPROVED status,
    exact fingerprint match, never consumed. Pending, rejected, cancelled,
    and expired approvals never authorize."""
    for approval in approvals:
        if (
            approval.status is ApprovalStatus.APPROVED
            and approval.authorization_fingerprint == fingerprint
            and not approval.is_consumed
        ):
            return approval
    return None


class RequestToolApproval(LifecycleEvents):
    """Claim-aware approval creation for a protected tool action.

    Mirrors RequestApproval's coordination (guards, wait_for_approval,
    events) with two fencing differences: the claim must be active when the
    transaction opens, and the work item is removed via remove_if_claimed —
    if either check fails, ClaimLost aborts the transaction and nothing is
    persisted."""

    def execute(
        self,
        command: RequestApprovalCommand,
        *,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
    ) -> ApprovalRequestResult:
        with self._uow_factory() as uow:
            now = self._clock.now()
            if not uow.work_queue.is_claim_active(
                command.run_id, worker_id, claim_token, claim_generation, now
            ):
                raise ClaimLost("claim is no longer active; refusing to request approval")
            run = uow.runs.get(command.run_id)
            if run is None:
                raise RunNotFound(command.run_id)
            if run.status is not RunStatus.RUNNING:
                raise EntityConflict("run cannot wait for approval")
            step: RunStep | None = None
            if command.step_id is not None:
                step = uow.steps.get(command.step_id)
                if step is None:
                    raise RunStepNotFound(command.step_id)
                if step.run_id != run.id:
                    raise EntityConflict("step does not belong to run")
                if step.status is not RunStepStatus.RUNNING:
                    raise EntityConflict("step cannot wait for approval")
            if uow.approvals.list_pending_for_run(run.id):
                raise EntityConflict("run already has a pending approval")
            approval = ApprovalRequest.new(
                id=ApprovalRequestId.new(),
                run_id=run.id,
                category=command.category,
                summary=command.summary,
                reason=command.reason,
                requested_action=command.requested_action,
                requested_input=command.requested_input,
                requested_at=now,
                step_id=command.step_id,
                expires_at=command.expires_at,
                authorization_fingerprint=command.authorization_fingerprint,
            )
            uow.approvals.add(approval)
            run.wait_for_approval(now, approval.id)
            uow.runs.save(run)
            if not uow.work_queue.remove_if_claimed(
                run.id, worker_id, claim_token, claim_generation, now
            ):
                raise ClaimLost("claim lost while parking the run for approval")
            if step is not None:
                step.wait_for_approval(now, approval.id)
                uow.steps.save(step)
            self.append_run_events(
                uow,
                run,
                now,
                [
                    (
                        RunEventType.APPROVAL_REQUESTED,
                        {
                            "approval_request_id": str(approval.id),
                            "category": approval.category.value,
                            "step_id": str(approval.step_id) if approval.step_id else None,
                        },
                        approval.step_id,
                    ),
                    (
                        RunEventType.RUN_WAITING_FOR_APPROVAL,
                        {"run_id": str(run.id), "approval_request_id": str(approval.id)},
                        approval.step_id,
                    ),
                ],
            )
            uow.commit()
            return approval_result(approval)
