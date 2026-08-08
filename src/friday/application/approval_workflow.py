"""Approval workflow use cases: request and resolve human authorization.

Phase 8 records authorization state only — no requested action is executed,
no expiry scheduler runs, and nothing is resumed automatically beyond the
documented waiting -> running coordination below.

Resolution policy: every terminal resolution (approved, rejected, cancelled,
expired) returns a Run/RunStep still waiting on that approval to `running`
via the existing `resume()` transition. Acting on the resolution (proceeding
with, or abandoning, the requested action) is a later runtime concern. An
entity that no longer waits on the approval (e.g. cancelled meanwhile) is
left untouched.
"""

from __future__ import annotations

from datetime import datetime

from friday.application.commands import (
    ApproveRequestCommand,
    CancelApprovalCommand,
    ExpireApprovalCommand,
    RejectRequestCommand,
    RequestApprovalCommand,
)
from friday.application.errors import (
    ApprovalNotFound,
    EntityConflict,
    RunNotFound,
    RunStepNotFound,
)
from friday.application.lifecycle_events import LifecycleEvents
from friday.application.ports import UnitOfWork
from friday.application.results import ApprovalRequestResult
from friday.domain.approval import ApprovalRequest, ApprovalStatus
from friday.domain.event import RunEventType
from friday.domain.identifiers import ApprovalRequestId, RunId, RunStepId
from friday.domain.json_value import JsonValue
from friday.domain.run import RunStatus
from friday.domain.step import RunStep, RunStepStatus


def approval_result(approval: ApprovalRequest) -> ApprovalRequestResult:
    return ApprovalRequestResult(
        approval.id,
        approval.run_id,
        approval.step_id,
        approval.category,
        approval.summary,
        approval.reason,
        approval.requested_action,
        approval.requested_input,
        approval.status,
        approval.requested_at,
        approval.expires_at,
        approval.resolved_at,
        approval.resolution_note,
        approval.resolver,
        approval.authorization_fingerprint,
        approval.consumed_at,
        approval.subject_kind,
        approval.subject_id,
    )


class GetApproval(LifecycleEvents):
    def execute(self, approval_id: ApprovalRequestId) -> ApprovalRequestResult:
        with self._uow_factory() as uow:
            approval = uow.approvals.get(approval_id)
            if approval is None:
                raise ApprovalNotFound(approval_id)
            return approval_result(approval)


class ListPendingApprovalsForRun(LifecycleEvents):
    def execute(self, run_id: RunId) -> list[ApprovalRequestResult]:
        with self._uow_factory() as uow:
            if uow.runs.get(run_id) is None:
                raise RunNotFound(run_id)
            return [approval_result(a) for a in uow.approvals.list_pending_for_run(run_id)]


class ListApprovalsForRun(LifecycleEvents):
    def execute(self, run_id: RunId) -> list[ApprovalRequestResult]:
        with self._uow_factory() as uow:
            if uow.runs.get(run_id) is None:
                raise RunNotFound(run_id)
            return [approval_result(a) for a in uow.approvals.list_for_run(run_id)]

    def page(
        self, run_id: RunId, limit: int, after_requested_at: datetime | None, after_id: str | None
    ) -> list[ApprovalRequestResult]:
        with self._uow_factory() as uow:
            if uow.runs.get(run_id) is None:
                raise RunNotFound(run_id)
            return [
                approval_result(a)
                for a in uow.approvals.list_for_run_page(
                    run_id, limit, after_requested_at, after_id
                )
            ]


class RequestApproval(LifecycleEvents):
    def execute(self, command: RequestApprovalCommand) -> ApprovalRequestResult:
        with self._uow_factory() as uow:
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
            now = self._clock.now()
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
            uow.work_queue.remove(run.id)
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
                        _approval_payload(approval),
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


def _approval_payload(approval: ApprovalRequest) -> JsonValue:
    return {
        "approval_request_id": str(approval.id),
        "category": approval.category.value,
        "step_id": str(approval.step_id) if approval.step_id else None,
    }


def transition_approval_in_transaction(
    uow: UnitOfWork,
    approval: ApprovalRequest,
    target: ApprovalStatus,
    now: datetime,
    *,
    resolver: str | None = None,
    resolution_note: str | None = None,
) -> ApprovalRequest:
    """Stage a canonical ApprovalRequest transition without committing.

    Skill promotion and rollback approvals are non-run subjects, so their
    callers need the same domain transition rules without opening the public
    resolution use cases (which own a commit). Run-facing callers use this
    helper after validating their run/step coordination in ``_resolve``.
    """
    if approval.status is target:
        return approval
    if approval.status is not ApprovalStatus.PENDING:
        raise EntityConflict("approval request is terminal")
    if target is ApprovalStatus.APPROVED:
        if resolver is None:
            raise EntityConflict("approved approval requires a resolver")
        approval.approve(now, resolver, resolution_note)
    elif target is ApprovalStatus.REJECTED:
        if resolver is None:
            raise EntityConflict("rejected approval requires a resolver")
        approval.reject(now, resolver, resolution_note)
    elif target is ApprovalStatus.CANCELLED:
        approval.cancel(now, resolution_note)
    elif target is ApprovalStatus.EXPIRED:
        approval.expire(now)
    else:
        raise EntityConflict("approval request transition is not terminal")
    uow.approvals.save(approval)
    return approval


class _ApprovalResolution(LifecycleEvents):
    """Shared load/validate/coordinate mechanics for the four resolutions."""

    def _load(self, uow: UnitOfWork, approval_id: ApprovalRequestId) -> ApprovalRequest:
        approval = uow.approvals.get(approval_id)
        if approval is None:
            raise ApprovalNotFound(approval_id)
        return approval

    def _resolve(
        self,
        uow: UnitOfWork,
        approval: ApprovalRequest,
        target: ApprovalStatus,
        now: datetime,
        resolver: str | None,
        resolution_note: str | None,
    ) -> ApprovalRequestResult:
        """Apply the terminal transition, resume waiting entities, and append
        the canonical resolution event batch without nesting a commit."""
        if approval.status is target:
            uow.commit()
            return approval_result(approval)
        if approval.status is not ApprovalStatus.PENDING:
            raise EntityConflict("approval request is terminal")
        run = uow.runs.get(approval.run_id) if approval.run_id is not None else None
        if approval.run_id is not None and run is None:
            raise RunNotFound(approval.run_id)
        step: RunStep | None = None
        if approval.step_id is not None:
            if run is None:
                raise EntityConflict("non-run approval cannot reference a step")
            step = uow.steps.get(approval.step_id)
            if step is None:
                raise RunStepNotFound(approval.step_id)
            if step.run_id != run.id:
                raise EntityConflict("step does not belong to run")
        transition_approval_in_transaction(
            uow,
            approval,
            target,
            now,
            resolver=resolver,
            resolution_note=resolution_note,
        )
        if run is None:
            # Skill promotion/rollback approvals are canonical ApprovalRequest
            # subjects but do not fabricate a Task, Run, or RunEvent stream.
            uow.commit()
            return approval_result(approval)
        specs: list[tuple[RunEventType, JsonValue, RunStepId | None]] = [
            (
                RunEventType.APPROVAL_RESOLVED,
                {
                    "approval_request_id": str(approval.id),
                    "resolution": approval.status.value,
                },
                approval.step_id,
            )
        ]
        if (
            run is not None
            and step is not None
            and step.status is RunStepStatus.WAITING_FOR_APPROVAL
            and step.approval_request_id == approval.id
        ):
            step.resume(now)
            uow.steps.save(step)
        if (
            run is not None
            and run.status is RunStatus.WAITING_FOR_APPROVAL
            and run.approval_request_id == approval.id
        ):
            run.resume(now)
            uow.runs.save(run)
            uow.work_queue.enqueue(run.id, available_at=now, enqueued_at=now)
            specs.append(
                (
                    RunEventType.RUN_RESUMED,
                    {"run_id": str(run.id), "approval_request_id": str(approval.id)},
                    approval.step_id,
                )
            )
        self.append_run_events(uow, run, now, specs)
        uow.commit()
        return approval_result(approval)


class ApproveRequest(_ApprovalResolution):
    def execute(self, command: ApproveRequestCommand) -> ApprovalRequestResult:
        with self._uow_factory() as uow:
            approval = self._load(uow, command.approval_id)
            now = self._clock.now()
            return self._resolve(
                uow,
                approval,
                ApprovalStatus.APPROVED,
                now,
                command.resolver,
                command.resolution_note,
            )


class RejectRequest(_ApprovalResolution):
    def execute(self, command: RejectRequestCommand) -> ApprovalRequestResult:
        with self._uow_factory() as uow:
            approval = self._load(uow, command.approval_id)
            now = self._clock.now()
            return self._resolve(
                uow,
                approval,
                ApprovalStatus.REJECTED,
                now,
                command.resolver,
                command.resolution_note,
            )


class CancelApproval(_ApprovalResolution):
    def execute(self, command: CancelApprovalCommand) -> ApprovalRequestResult:
        with self._uow_factory() as uow:
            approval = self._load(uow, command.approval_id)
            now = self._clock.now()
            return self._resolve(
                uow,
                approval,
                ApprovalStatus.CANCELLED,
                now,
                None,
                command.resolution_note,
            )


class ExpireApproval(_ApprovalResolution):
    def execute(self, command: ExpireApprovalCommand) -> ApprovalRequestResult:
        with self._uow_factory() as uow:
            approval = self._load(uow, command.approval_id)
            now = self._clock.now()
            if approval.status is ApprovalStatus.PENDING:
                if approval.expires_at is None:
                    raise EntityConflict("approval request has no expiry deadline")
                if now < approval.expires_at:
                    raise EntityConflict("approval request expiry is not due")
            return self._resolve(
                uow,
                approval,
                ApprovalStatus.EXPIRED,
                now,
                None,
                None,
            )
