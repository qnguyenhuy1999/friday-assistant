"""Approval request/response models and their mapping to/from the
application layer. Never reused as commands or results themselves."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from friday.application.commands import (
    ApproveRequestCommand,
    CancelApprovalCommand,
    RejectRequestCommand,
    RequestApprovalCommand,
)
from friday.application.results import ApprovalRequestResult
from friday.domain.approval import ApprovalCategory, ApprovalStatus
from friday.domain.identifiers import ApprovalRequestId, RunId, RunStepId


class RequestApprovalBody(BaseModel):
    category: ApprovalCategory
    summary: str
    reason: str
    requested_action: str
    requested_input: Any = None
    step_id: str | None = None
    expires_at: datetime | None = None

    def to_command(self, run_id: RunId) -> RequestApprovalCommand:
        return RequestApprovalCommand(
            run_id=run_id,
            category=self.category,
            summary=self.summary,
            reason=self.reason,
            requested_action=self.requested_action,
            requested_input=self.requested_input,
            step_id=RunStepId.parse(self.step_id) if self.step_id is not None else None,
            expires_at=self.expires_at,
        )


class ResolveApprovalBody(BaseModel):
    resolver: str
    resolution_note: str | None = None

    def to_approve_command(self, approval_id: ApprovalRequestId) -> ApproveRequestCommand:
        return ApproveRequestCommand(
            approval_id=approval_id, resolver=self.resolver, resolution_note=self.resolution_note
        )

    def to_reject_command(self, approval_id: ApprovalRequestId) -> RejectRequestCommand:
        return RejectRequestCommand(
            approval_id=approval_id, resolver=self.resolver, resolution_note=self.resolution_note
        )


class CancelApprovalBody(BaseModel):
    resolution_note: str | None = None

    def to_command(self, approval_id: ApprovalRequestId) -> CancelApprovalCommand:
        return CancelApprovalCommand(approval_id=approval_id, resolution_note=self.resolution_note)


class ApprovalResponse(BaseModel):
    approval_id: str
    run_id: str
    step_id: str | None
    category: ApprovalCategory
    summary: str
    reason: str
    requested_action: str
    requested_input: Any
    status: ApprovalStatus
    requested_at: datetime
    expires_at: datetime | None
    resolved_at: datetime | None
    resolution_note: str | None
    resolver: str | None

    @classmethod
    def from_result(cls, result: ApprovalRequestResult) -> ApprovalResponse:
        return cls(
            approval_id=str(result.approval_id),
            run_id=str(result.run_id),
            step_id=str(result.step_id) if result.step_id is not None else None,
            category=result.category,
            summary=result.summary,
            reason=result.reason,
            requested_action=result.requested_action,
            requested_input=result.requested_input,
            status=result.status,
            requested_at=result.requested_at,
            expires_at=result.expires_at,
            resolved_at=result.resolved_at,
            resolution_note=result.resolution_note,
            resolver=result.resolver,
        )


class ApprovalPage(BaseModel):
    items: list[ApprovalResponse]
    next_cursor: str | None
