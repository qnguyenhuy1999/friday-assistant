"""Immutable use-case output results. Expose typed domain identifiers only —
never ORM row instances or raw dicts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from friday.domain.approval import ApprovalCategory, ApprovalStatus
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
from friday.domain.run import RunStatus
from friday.domain.step import RunStepStatus
from friday.domain.task import TaskStatus
from friday.domain.tool import ToolInvocationStatus


@dataclass(frozen=True, slots=True)
class CreateTaskResult:
    task_id: TaskId


@dataclass(frozen=True, slots=True)
class StartRunResult:
    task_id: TaskId
    run_id: RunId


@dataclass(frozen=True, slots=True)
class TaskResult:
    task_id: TaskId
    title: str
    description: str
    status: TaskStatus
    created_at: datetime
    failure: Failure | None


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: RunId
    task_id: TaskId
    status: RunStatus
    created_at: datetime
    failure: Failure | None


@dataclass(frozen=True, slots=True)
class RunStepResult:
    step_id: RunStepId
    run_id: RunId
    name: str
    position: int
    status: RunStepStatus
    failure: Failure | None


@dataclass(frozen=True, slots=True)
class ApprovalRequestResult:
    approval_id: ApprovalRequestId
    run_id: RunId
    step_id: RunStepId | None
    category: ApprovalCategory
    summary: str
    reason: str
    requested_action: str
    requested_input: JsonValue
    status: ApprovalStatus
    requested_at: datetime
    expires_at: datetime | None
    resolved_at: datetime | None
    resolution_note: str | None
    resolver: str | None
    authorization_fingerprint: str | None
    consumed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ToolInvocationResult:
    invocation_id: ToolInvocationId
    run_id: RunId
    step_id: RunStepId | None
    tool_name: str
    status: ToolInvocationStatus
    requested_at: datetime
    approval_request_id: ApprovalRequestId | None
    output: JsonValue
    output_set: bool
    failure: Failure | None


@dataclass(frozen=True, slots=True)
class RunClaimResult:
    run_id: RunId
    task_id: TaskId
    worker_id: str
    claim_token: str
    claim_generation: int
    attempt_number: int
    acquired_at: datetime
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class ArtifactResult:
    artifact_id: ArtifactId
    run_id: RunId
    step_id: RunStepId | None
    kind: ArtifactKind
    name: str
    media_type: str
    location: str
    created_at: datetime
    size: int | None
    checksum: str | None
    metadata: JsonValue
