"""Durable Workflow execution snapshots and node lifecycle values.

Workflow definitions describe a graph, but they do not confer execution
authority.  These values record the exact immutable graph and Agent revision
snapshot Friday selected, together with the durable state of its ordinary
child Runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.identifiers import (
    AgentId,
    AgentRevisionId,
    RunId,
    RunWorkflowResolutionId,
    TaskId,
    WorkflowExecutionId,
    WorkflowId,
    WorkflowNodeExecutionId,
    WorkflowNodeId,
    WorkflowRevisionId,
)
from friday.domain.json_value import JsonValue, ensure_json_value
from friday.domain.time import ensure_utc

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_NODE_KEY = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")


class WorkflowExecutionStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowNodeExecutionStatus(StrEnum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


TERMINAL_WORKFLOW_EXECUTION_STATUSES = frozenset(
    {
        WorkflowExecutionStatus.SUCCEEDED,
        WorkflowExecutionStatus.FAILED,
        WorkflowExecutionStatus.CANCELLED,
    }
)

TERMINAL_WORKFLOW_NODE_EXECUTION_STATUSES = frozenset(
    {
        WorkflowNodeExecutionStatus.SUCCEEDED,
        WorkflowNodeExecutionStatus.FAILED,
        WorkflowNodeExecutionStatus.CANCELLED,
        WorkflowNodeExecutionStatus.BLOCKED,
    }
)


def _validate_sha256(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise DomainValidationError(f"{field_name} must be a lowercase sha256")
    return value


def _validate_failure(code: str | None, message: str | None, *, required: bool) -> None:
    if required and (not code or not message):
        raise DomainValidationError("failed workflow state requires failure code and message")
    if code is not None and not code.strip():
        raise DomainValidationError("failure code must not be empty")
    if message is not None and not message.strip():
        raise DomainValidationError("failure message must not be empty")


@dataclass(slots=True)
class WorkflowExecution:
    id: WorkflowExecutionId
    root_run_id: RunId
    workflow_id: WorkflowId
    workflow_revision_id: WorkflowRevisionId
    workflow_content_sha256: str
    status: WorkflowExecutionStatus
    started_at: datetime
    completed_at: datetime | None = None
    failure_code: str | None = None
    failure_message: str | None = None

    def __post_init__(self) -> None:
        self.workflow_content_sha256 = _validate_sha256(
            self.workflow_content_sha256, "WorkflowExecution.workflow_content_sha256"
        )
        self.started_at = ensure_utc(self.started_at)
        if self.completed_at is not None:
            self.completed_at = ensure_utc(self.completed_at)
            if self.completed_at < self.started_at:
                raise DomainValidationError(
                    "WorkflowExecution.completed_at precedes WorkflowExecution.started_at"
                )
        if self.status is WorkflowExecutionStatus.RUNNING:
            if self.completed_at is not None:
                raise DomainValidationError("running WorkflowExecution cannot be completed")
            if self.failure_code is not None or self.failure_message is not None:
                raise DomainValidationError("running WorkflowExecution cannot have a failure")
        elif self.completed_at is None:
            raise DomainValidationError("terminal WorkflowExecution requires completed_at")
        if self.status is WorkflowExecutionStatus.SUCCEEDED and (
            self.failure_code is not None or self.failure_message is not None
        ):
            raise DomainValidationError("succeeded WorkflowExecution cannot have a failure")
        if self.status is WorkflowExecutionStatus.CANCELLED and self.failure_code is not None:
            raise DomainValidationError("cancelled WorkflowExecution cannot have a failure code")
        _validate_failure(
            self.failure_code,
            self.failure_message,
            required=self.status is WorkflowExecutionStatus.FAILED,
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_WORKFLOW_EXECUTION_STATUSES

    def _require_running(self, target: WorkflowExecutionStatus) -> None:
        if self.status is not WorkflowExecutionStatus.RUNNING:
            raise InvalidStateTransition("WorkflowExecution", self.status.value, target.value)

    def succeed(self, at: datetime) -> None:
        self._require_running(WorkflowExecutionStatus.SUCCEEDED)
        self.completed_at = ensure_utc(at)
        if self.completed_at < self.started_at:
            raise DomainValidationError(
                "WorkflowExecution.completed_at precedes WorkflowExecution.started_at"
            )
        self.failure_code = None
        self.failure_message = None
        self.status = WorkflowExecutionStatus.SUCCEEDED

    def fail(self, at: datetime, failure_code: str, failure_message: str) -> None:
        self._require_running(WorkflowExecutionStatus.FAILED)
        _validate_failure(failure_code, failure_message, required=True)
        completed_at = ensure_utc(at)
        if completed_at < self.started_at:
            raise DomainValidationError(
                "WorkflowExecution.completed_at precedes WorkflowExecution.started_at"
            )
        self.completed_at = completed_at
        self.failure_code = failure_code
        self.failure_message = failure_message
        self.status = WorkflowExecutionStatus.FAILED

    def cancel(self, at: datetime, message: str | None = None) -> None:
        self._require_running(WorkflowExecutionStatus.CANCELLED)
        completed_at = ensure_utc(at)
        if completed_at < self.started_at:
            raise DomainValidationError(
                "WorkflowExecution.completed_at precedes WorkflowExecution.started_at"
            )
        if message is not None and not message.strip():
            raise DomainValidationError("WorkflowExecution cancellation message must not be empty")
        self.completed_at = completed_at
        self.failure_code = None
        self.failure_message = message
        self.status = WorkflowExecutionStatus.CANCELLED


@dataclass(slots=True)
class WorkflowNodeExecution:
    id: WorkflowNodeExecutionId
    workflow_execution_id: WorkflowExecutionId
    workflow_node_id: WorkflowNodeId
    # Frozen structural ownership: the exact Workflow revision this node
    # execution's node must belong to (must equal the owning WorkflowExecution's
    # frozen workflow_revision_id).
    workflow_revision_id: WorkflowRevisionId
    node_key: str
    target_agent_id: AgentId
    target_agent_revision_id: AgentRevisionId
    target_agent_revision_sha256: str
    status: WorkflowNodeExecutionStatus
    created_at: datetime
    child_task_id: TaskId | None = None
    child_run_id: RunId | None = None
    child_execution_id: RunId | None = None
    result_payload: JsonValue = None
    failure_code: str | None = None
    failure_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        if (
            not self.node_key
            or len(self.node_key) > 128
            or _NODE_KEY.fullmatch(self.node_key) is None
        ):
            raise DomainValidationError("WorkflowNodeExecution.node_key is invalid")
        self.target_agent_revision_sha256 = _validate_sha256(
            self.target_agent_revision_sha256,
            "WorkflowNodeExecution.target_agent_revision_sha256",
        )
        self.created_at = ensure_utc(self.created_at)
        if self.started_at is not None:
            self.started_at = ensure_utc(self.started_at)
            if self.started_at < self.created_at:
                raise DomainValidationError("WorkflowNodeExecution.started_at precedes created_at")
        if self.completed_at is not None:
            self.completed_at = ensure_utc(self.completed_at)
            if self.completed_at < (self.started_at or self.created_at):
                raise DomainValidationError(
                    "WorkflowNodeExecution.completed_at precedes start/creation time"
                )
        self.result_payload = ensure_json_value(
            self.result_payload, path="WorkflowNodeExecution.result_payload"
        )
        _validate_failure(self.failure_code, self.failure_message, required=False)
        self._validate_status_shape()

    def _validate_status_shape(self) -> None:
        has_child = (
            self.child_task_id is not None
            or self.child_run_id is not None
            or self.child_execution_id is not None
        )
        all_child = (
            self.child_task_id is not None
            and self.child_run_id is not None
            and self.child_execution_id is not None
        )
        if self.status is WorkflowNodeExecutionStatus.PENDING:
            if has_child or self.started_at is not None or self.completed_at is not None:
                raise DomainValidationError("pending WorkflowNodeExecution cannot have a child")
            if self.failure_code is not None or self.failure_message is not None:
                raise DomainValidationError("pending WorkflowNodeExecution cannot have a failure")
        elif self.status is WorkflowNodeExecutionStatus.DISPATCHED:
            if not all_child or self.started_at is None or self.completed_at is not None:
                raise DomainValidationError(
                    "dispatched WorkflowNodeExecution child shape is invalid"
                )
            if self.failure_code is not None or self.failure_message is not None:
                raise DomainValidationError(
                    "dispatched WorkflowNodeExecution cannot have a failure"
                )
        elif self.status is WorkflowNodeExecutionStatus.SUCCEEDED:
            if not all_child or self.started_at is None or self.completed_at is None:
                raise DomainValidationError(
                    "succeeded WorkflowNodeExecution child shape is invalid"
                )
            if self.failure_code is not None or self.failure_message is not None:
                raise DomainValidationError("succeeded WorkflowNodeExecution cannot have a failure")
        elif self.status is WorkflowNodeExecutionStatus.FAILED:
            if not all_child or self.started_at is None or self.completed_at is None:
                raise DomainValidationError("failed WorkflowNodeExecution child shape is invalid")
            _validate_failure(self.failure_code, self.failure_message, required=True)
        elif self.status is WorkflowNodeExecutionStatus.CANCELLED:
            if self.completed_at is None:
                raise DomainValidationError("cancelled WorkflowNodeExecution requires completed_at")
            if self.failure_code is not None or self.failure_message is not None:
                raise DomainValidationError("cancelled WorkflowNodeExecution cannot have a failure")
        elif self.status is WorkflowNodeExecutionStatus.BLOCKED:
            if self.completed_at is None:
                raise DomainValidationError("blocked WorkflowNodeExecution requires completed_at")
            if has_child:
                raise DomainValidationError("blocked WorkflowNodeExecution cannot have a child")
            _validate_failure(self.failure_code, self.failure_message, required=True)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_WORKFLOW_NODE_EXECUTION_STATUSES

    def _require_status(
        self, target: WorkflowNodeExecutionStatus, *allowed: WorkflowNodeExecutionStatus
    ) -> None:
        if self.status not in allowed:
            raise InvalidStateTransition("WorkflowNodeExecution", self.status.value, target.value)

    def dispatch(
        self,
        child_task_id: TaskId,
        child_run_id: RunId,
        child_execution_id: RunId,
        at: datetime,
    ) -> None:
        self._require_status(
            WorkflowNodeExecutionStatus.DISPATCHED,
            WorkflowNodeExecutionStatus.PENDING,
        )
        if (
            self.child_task_id is not None
            or self.child_run_id is not None
            or self.child_execution_id is not None
        ):
            raise DomainValidationError("WorkflowNodeExecution has already been dispatched")
        started_at = ensure_utc(at)
        if started_at < self.created_at:
            raise DomainValidationError("WorkflowNodeExecution.started_at precedes created_at")
        self.child_task_id = child_task_id
        self.child_run_id = child_run_id
        self.child_execution_id = child_execution_id
        self.started_at = started_at
        self.completed_at = None
        self.failure_code = None
        self.failure_message = None
        self.status = WorkflowNodeExecutionStatus.DISPATCHED

    def succeed(self, at: datetime, result_payload: JsonValue = None) -> None:
        self._require_status(
            WorkflowNodeExecutionStatus.SUCCEEDED,
            WorkflowNodeExecutionStatus.DISPATCHED,
        )
        completed_at = ensure_utc(at)
        if completed_at < (self.started_at or self.created_at):
            raise DomainValidationError(
                "WorkflowNodeExecution.completed_at precedes start/creation time"
            )
        self.result_payload = ensure_json_value(
            result_payload, path="WorkflowNodeExecution.result_payload"
        )
        self.completed_at = completed_at
        self.failure_code = None
        self.failure_message = None
        self.status = WorkflowNodeExecutionStatus.SUCCEEDED

    def fail(self, at: datetime, failure_code: str, failure_message: str) -> None:
        self._require_status(
            WorkflowNodeExecutionStatus.FAILED,
            WorkflowNodeExecutionStatus.DISPATCHED,
        )
        _validate_failure(failure_code, failure_message, required=True)
        completed_at = ensure_utc(at)
        if completed_at < (self.started_at or self.created_at):
            raise DomainValidationError(
                "WorkflowNodeExecution.completed_at precedes start/creation time"
            )
        self.completed_at = completed_at
        self.failure_code = failure_code
        self.failure_message = failure_message
        self.status = WorkflowNodeExecutionStatus.FAILED

    def cancel(self, at: datetime) -> None:
        self._require_status(
            WorkflowNodeExecutionStatus.CANCELLED,
            WorkflowNodeExecutionStatus.PENDING,
            WorkflowNodeExecutionStatus.DISPATCHED,
        )
        completed_at = ensure_utc(at)
        if completed_at < (self.started_at or self.created_at):
            raise DomainValidationError(
                "WorkflowNodeExecution.completed_at precedes start/creation time"
            )
        self.completed_at = completed_at
        self.failure_code = None
        self.failure_message = None
        self.status = WorkflowNodeExecutionStatus.CANCELLED

    def block(
        self,
        at: datetime,
        failure_code: str = "blocked_by_predecessor",
        failure_message: str = "A predecessor Workflow node did not succeed",
    ) -> None:
        self._require_status(
            WorkflowNodeExecutionStatus.BLOCKED, WorkflowNodeExecutionStatus.PENDING
        )
        _validate_failure(failure_code, failure_message, required=True)
        completed_at = ensure_utc(at)
        if completed_at < self.created_at:
            raise DomainValidationError("WorkflowNodeExecution.completed_at precedes created_at")
        self.completed_at = completed_at
        self.failure_code = failure_code
        self.failure_message = failure_message
        self.status = WorkflowNodeExecutionStatus.BLOCKED


@dataclass(frozen=True, slots=True)
class RunWorkflowResolution:
    id: RunWorkflowResolutionId
    run_id: RunId
    workflow_id: WorkflowId
    workflow_revision_id: WorkflowRevisionId
    content_sha256: str
    resolved_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "content_sha256",
            _validate_sha256(self.content_sha256, "RunWorkflowResolution.content_sha256"),
        )
        object.__setattr__(self, "resolved_at", ensure_utc(self.resolved_at))


@dataclass(frozen=True, slots=True)
class TaskWorkflowBinding:
    task_id: TaskId
    workflow_id: WorkflowId
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        created_at = ensure_utc(self.created_at)
        updated_at = ensure_utc(self.updated_at)
        if updated_at < created_at:
            raise DomainValidationError(
                "TaskWorkflowBinding.updated_at precedes TaskWorkflowBinding.created_at"
            )
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)

    @classmethod
    def new(cls, *, task_id: TaskId, workflow_id: WorkflowId, at: datetime) -> TaskWorkflowBinding:
        now = ensure_utc(at)
        return cls(task_id, workflow_id, now, now)

    def updated(self, *, workflow_id: WorkflowId, at: datetime) -> TaskWorkflowBinding:
        return TaskWorkflowBinding(self.task_id, workflow_id, self.created_at, at)
