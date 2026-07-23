"""Domain layer: business types, rules, and domain-owned interfaces.

Must not import friday.application, friday.infrastructure, or any apps.* module.
Re-exports the public domain surface; internal helpers are not re-exported.
"""

from __future__ import annotations

from friday.domain.approval import ApprovalCategory, ApprovalRequest, ApprovalStatus
from friday.domain.artifact import Artifact, ArtifactKind
from friday.domain.errors import DomainError, DomainValidationError, InvalidStateTransition
from friday.domain.event import RunEvent, RunEventType
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import (
    ApprovalRequestId,
    ArtifactId,
    RunEventId,
    RunId,
    RunStepId,
    TaskEventId,
    TaskId,
    ToolInvocationId,
)
from friday.domain.json_value import JsonScalar, JsonValue, ensure_json_value
from friday.domain.run import Run, RunStatus
from friday.domain.step import RunStep, RunStepStatus
from friday.domain.task import Task, TaskStatus
from friday.domain.task_event import TaskEvent, TaskEventType
from friday.domain.time import ensure_utc
from friday.domain.tool import ToolInvocation, ToolInvocationStatus

__all__ = [
    "ApprovalCategory",
    "ApprovalRequest",
    "ApprovalRequestId",
    "ApprovalStatus",
    "Artifact",
    "ArtifactId",
    "ArtifactKind",
    "DomainError",
    "DomainValidationError",
    "Failure",
    "FailureCause",
    "InvalidStateTransition",
    "JsonScalar",
    "JsonValue",
    "Run",
    "RunEvent",
    "RunEventId",
    "RunEventType",
    "RunId",
    "RunStatus",
    "RunStep",
    "RunStepId",
    "RunStepStatus",
    "Task",
    "TaskEvent",
    "TaskEventId",
    "TaskEventType",
    "TaskId",
    "TaskStatus",
    "ToolInvocation",
    "ToolInvocationId",
    "ToolInvocationStatus",
    "ensure_json_value",
    "ensure_utc",
]
