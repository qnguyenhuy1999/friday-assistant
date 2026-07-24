"""ToolInvocation request/response models and application-layer mapping."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from friday.application.commands import (
    MarkToolInvocationFailedCommand,
    MarkToolInvocationSucceededCommand,
    RequestToolInvocationCommand,
)
from friday.application.results import ToolInvocationResult
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import ApprovalRequestId, RunId, RunStepId, ToolInvocationId
from friday.domain.tool import ToolInvocationStatus


class FailureBody(BaseModel):
    code: str
    message: str
    retryable: bool
    cause: FailureCause
    details: Any = None

    def to_domain(self) -> Failure:
        return Failure(
            code=self.code,
            message=self.message,
            retryable=self.retryable,
            cause=self.cause,
            details=self.details,
        )


class FailureResponse(BaseModel):
    code: str
    message: str
    retryable: bool
    cause: FailureCause
    details: Any

    @classmethod
    def from_domain(cls, failure: Failure) -> FailureResponse:
        return cls(
            code=failure.code,
            message=failure.message,
            retryable=failure.retryable,
            cause=failure.cause,
            details=failure.details,
        )


class RequestToolInvocationBody(BaseModel):
    tool_name: str
    requested_input: Any = None
    step_id: UUID | None = None
    approval_request_id: UUID | None = None

    def to_command(self, run_id: RunId) -> RequestToolInvocationCommand:
        return RequestToolInvocationCommand(
            run_id=run_id,
            tool_name=self.tool_name,
            requested_input=self.requested_input,
            step_id=RunStepId.parse(str(self.step_id)) if self.step_id is not None else None,
            approval_request_id=(
                ApprovalRequestId.parse(str(self.approval_request_id))
                if self.approval_request_id is not None
                else None
            ),
        )


class MarkSucceededBody(BaseModel):
    output: Any = None

    def to_command(self, invocation_id: ToolInvocationId) -> MarkToolInvocationSucceededCommand:
        return MarkToolInvocationSucceededCommand(invocation_id=invocation_id, output=self.output)


class MarkFailedBody(BaseModel):
    failure: FailureBody

    def to_command(self, invocation_id: ToolInvocationId) -> MarkToolInvocationFailedCommand:
        return MarkToolInvocationFailedCommand(
            invocation_id=invocation_id, failure=self.failure.to_domain()
        )


class ToolInvocationResponse(BaseModel):
    invocation_id: str
    run_id: str
    step_id: str | None
    tool_name: str
    status: ToolInvocationStatus
    requested_at: datetime
    approval_request_id: str | None
    output: Any
    output_set: bool
    failure: FailureResponse | None

    @classmethod
    def from_result(cls, result: ToolInvocationResult) -> ToolInvocationResponse:
        return cls(
            invocation_id=str(result.invocation_id),
            run_id=str(result.run_id),
            step_id=str(result.step_id) if result.step_id is not None else None,
            tool_name=result.tool_name,
            status=result.status,
            requested_at=result.requested_at,
            approval_request_id=(
                str(result.approval_request_id) if result.approval_request_id is not None else None
            ),
            output=result.output,
            output_set=result.output_set,
            failure=FailureResponse.from_domain(result.failure)
            if result.failure is not None
            else None,
        )


class ToolInvocationPage(BaseModel):
    items: list[ToolInvocationResponse]
    next_cursor: str | None
