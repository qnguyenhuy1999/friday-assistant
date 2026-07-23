"""Immutable use-case input commands. Stdlib dataclasses only — no
Pydantic/FastAPI/ORM/dict/vendor types, no generic command bus."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from friday.domain.approval import ApprovalCategory
from friday.domain.artifact import ArtifactKind
from friday.domain.failure import Failure
from friday.domain.identifiers import (
    ApprovalRequestId,
    ArtifactId,
    RunId,
    RunStepId,
    TaskId,
    ToolInvocationId,
)
from friday.domain.json_value import JsonValue


@dataclass(frozen=True, slots=True)
class CreateTaskCommand:
    title: str
    description: str


@dataclass(frozen=True, slots=True)
class StartRunCommand:
    task_id: TaskId


@dataclass(frozen=True, slots=True)
class CancelTaskCommand:
    task_id: TaskId


@dataclass(frozen=True, slots=True)
class CompleteTaskCommand:
    task_id: TaskId


@dataclass(frozen=True, slots=True)
class FailTaskCommand:
    task_id: TaskId
    failure: Failure


@dataclass(frozen=True, slots=True)
class StartQueuedRunCommand:
    run_id: RunId


@dataclass(frozen=True, slots=True)
class CompleteRunCommand:
    run_id: RunId


@dataclass(frozen=True, slots=True)
class FailRunCommand:
    run_id: RunId
    failure: Failure


@dataclass(frozen=True, slots=True)
class CancelRunCommand:
    run_id: RunId


@dataclass(frozen=True, slots=True)
class RetryFailedRunCommand:
    run_id: RunId


@dataclass(frozen=True, slots=True)
class CreateOrderedStepCommand:
    run_id: RunId
    name: str


@dataclass(frozen=True, slots=True)
class StartStepCommand:
    step_id: RunStepId


@dataclass(frozen=True, slots=True)
class CompleteStepCommand:
    step_id: RunStepId


@dataclass(frozen=True, slots=True)
class FailStepCommand:
    step_id: RunStepId
    failure: Failure


@dataclass(frozen=True, slots=True)
class SkipPendingStepCommand:
    step_id: RunStepId


@dataclass(frozen=True, slots=True)
class CancelStepCommand:
    step_id: RunStepId


@dataclass(frozen=True, slots=True)
class RequestApprovalCommand:
    run_id: RunId
    category: ApprovalCategory
    summary: str
    reason: str
    requested_action: str
    requested_input: JsonValue
    step_id: RunStepId | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ApproveRequestCommand:
    approval_id: ApprovalRequestId
    resolver: str
    resolution_note: str | None = None


@dataclass(frozen=True, slots=True)
class RejectRequestCommand:
    approval_id: ApprovalRequestId
    resolver: str
    resolution_note: str | None = None


@dataclass(frozen=True, slots=True)
class CancelApprovalCommand:
    approval_id: ApprovalRequestId
    resolution_note: str | None = None


@dataclass(frozen=True, slots=True)
class ExpireApprovalCommand:
    approval_id: ApprovalRequestId


@dataclass(frozen=True, slots=True)
class RequestToolInvocationCommand:
    run_id: RunId
    tool_name: str
    requested_input: JsonValue
    step_id: RunStepId | None = None
    approval_request_id: ApprovalRequestId | None = None


@dataclass(frozen=True, slots=True)
class MarkToolInvocationRunningCommand:
    invocation_id: ToolInvocationId


@dataclass(frozen=True, slots=True)
class MarkToolInvocationSucceededCommand:
    invocation_id: ToolInvocationId
    output: JsonValue


@dataclass(frozen=True, slots=True)
class MarkToolInvocationFailedCommand:
    invocation_id: ToolInvocationId
    failure: Failure


@dataclass(frozen=True, slots=True)
class CancelToolInvocationCommand:
    invocation_id: ToolInvocationId


@dataclass(frozen=True, slots=True)
class RecordArtifactCommand:
    """`artifact_id` is caller-supplied only for idempotent replay of the same
    recording; when None a fresh identity is allocated."""

    run_id: RunId
    kind: ArtifactKind
    name: str
    media_type: str
    location: str
    step_id: RunStepId | None = None
    size: int | None = None
    checksum: str | None = None
    metadata: JsonValue = None
    artifact_id: ArtifactId | None = None
