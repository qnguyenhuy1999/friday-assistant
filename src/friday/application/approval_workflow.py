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

from collections.abc import Callable
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
            )
            uow.approvals.add(approval)
            run.wait_for_approval(now, approval.id)
            uow.runs.save(run)
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
        transition: Callable[[], None],
        now: datetime,
    ) -> ApprovalRequestResult:
        """Apply the terminal transition, resume waiting entities, and append
        the canonical resolution event batch. `transition` is a zero-argument
        callable staged by the caller (already bound to timestamps/resolver)."""
        if approval.status is target:
            uow.commit()
            return approval_result(approval)
        if approval.status is not ApprovalStatus.PENDING:
            raise EntityConflict("approval request is terminal")
        run = uow.runs.get(approval.run_id)
        if run is None:
            raise RunNotFound(approval.run_id)
        step: RunStep | None = None
        if approval.step_id is not None:
            step = uow.steps.get(approval.step_id)
            if step is None:
                raise RunStepNotFound(approval.step_id)
            if step.run_id != run.id:
                raise EntityConflict("step does not belong to run")
        transition()
        uow.approvals.save(approval)
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
            step is not None
            and step.status is RunStepStatus.WAITING_FOR_APPROVAL
            and step.approval_request_id == approval.id
        ):
            step.resume(now)
            uow.steps.save(step)
        if run.status is RunStatus.WAITING_FOR_APPROVAL and run.approval_request_id == approval.id:
            run.resume(now)
            uow.runs.save(run)
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
                lambda: approval.approve(now, command.resolver, command.resolution_note),
                now,
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
                lambda: approval.reject(now, command.resolver, command.resolution_note),
                now,
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
                lambda: approval.cancel(now, command.resolution_note),
                now,
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
                lambda: approval.expire(now),
                now,
            )
