"""Run: one execution attempt for a Task. Owns execution lifecycle state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.failure import Failure
from friday.domain.identifiers import (
    ApprovalRequestId,
    DelegationRequestId,
    RunId,
    TaskId,
    WorkflowExecutionId,
)
from friday.domain.time import ensure_utc


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    WAITING_FOR_DELEGATION = "waiting_for_delegation"
    WAITING_FOR_WORKFLOW = "waiting_for_workflow"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_RUN_STATUSES = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED})


@dataclass(slots=True)
class Run:
    _id: RunId
    _task_id: TaskId
    _execution_id: RunId
    _status: RunStatus
    _created_at: datetime
    _started_at: datetime | None = field(default=None)
    _ended_at: datetime | None = field(default=None)
    _failure: Failure | None = field(default=None)
    _approval_request_id: ApprovalRequestId | None = field(default=None)
    _delegation_request_id: DelegationRequestId | None = field(default=None)
    _workflow_execution_id: WorkflowExecutionId | None = field(default=None)

    @classmethod
    def new(
        cls, *, id: RunId, task_id: TaskId, created_at: datetime, execution_id: RunId | None = None
    ) -> Run:
        return cls(
            _id=id,
            _task_id=task_id,
            _execution_id=execution_id or id,
            _status=RunStatus.QUEUED,
            _created_at=ensure_utc(created_at),
        )

    @property
    def id(self) -> RunId:
        return self._id

    @property
    def task_id(self) -> TaskId:
        return self._task_id

    @property
    def execution_id(self) -> RunId:
        """Stable root id shared by all attempts of one execution."""
        return self._execution_id

    @property
    def status(self) -> RunStatus:
        return self._status

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def started_at(self) -> datetime | None:
        return self._started_at

    @property
    def ended_at(self) -> datetime | None:
        return self._ended_at

    @property
    def failure(self) -> Failure | None:
        return self._failure

    @property
    def approval_request_id(self) -> ApprovalRequestId | None:
        return self._approval_request_id

    @property
    def delegation_request_id(self) -> DelegationRequestId | None:
        return self._delegation_request_id

    @property
    def workflow_execution_id(self) -> WorkflowExecutionId | None:
        return self._workflow_execution_id

    def _require_status(self, *allowed: RunStatus, target: RunStatus) -> None:
        if self._status not in allowed:
            raise InvalidStateTransition("Run", self._status.value, target.value)

    def start(self, at: datetime) -> None:
        self._require_status(RunStatus.QUEUED, target=RunStatus.RUNNING)
        self._started_at = ensure_utc(at)
        self._status = RunStatus.RUNNING

    def wait_for_approval(self, at: datetime, approval_request_id: ApprovalRequestId) -> None:
        self._require_status(RunStatus.RUNNING, target=RunStatus.WAITING_FOR_APPROVAL)
        if self._delegation_request_id is not None or self._workflow_execution_id is not None:
            raise DomainValidationError(
                "approval, delegation, and workflow wait markers are mutually exclusive"
            )
        ensure_utc(at)
        self._approval_request_id = approval_request_id
        self._status = RunStatus.WAITING_FOR_APPROVAL

    def wait_for_delegation(self, at: datetime, delegation_request_id: DelegationRequestId) -> None:
        self._require_status(RunStatus.RUNNING, target=RunStatus.WAITING_FOR_DELEGATION)
        if self._approval_request_id is not None or self._workflow_execution_id is not None:
            raise DomainValidationError(
                "approval, delegation, and workflow wait markers are mutually exclusive"
            )
        ensure_utc(at)
        self._delegation_request_id = delegation_request_id
        self._status = RunStatus.WAITING_FOR_DELEGATION

    def wait_for_workflow(self, at: datetime, workflow_execution_id: WorkflowExecutionId) -> None:
        self._require_status(RunStatus.RUNNING, target=RunStatus.WAITING_FOR_WORKFLOW)
        if self._approval_request_id is not None or self._delegation_request_id is not None:
            raise DomainValidationError("workflow and other wait markers are mutually exclusive")
        if self._workflow_execution_id is not None:
            raise DomainValidationError("Run already has a Workflow execution owner")
        ensure_utc(at)
        self._workflow_execution_id = workflow_execution_id
        self._status = RunStatus.WAITING_FOR_WORKFLOW

    def resume(self, at: datetime) -> None:
        self._require_status(
            RunStatus.WAITING_FOR_APPROVAL,
            RunStatus.WAITING_FOR_DELEGATION,
            target=RunStatus.RUNNING,
        )
        ensure_utc(at)
        self._approval_request_id = None
        self._delegation_request_id = None
        self._status = RunStatus.RUNNING

    def resume_from_delegation(self, at: datetime) -> None:
        self._require_status(RunStatus.WAITING_FOR_DELEGATION, target=RunStatus.RUNNING)
        ensure_utc(at)
        self._delegation_request_id = None
        self._status = RunStatus.RUNNING

    def succeed(self, at: datetime) -> None:
        self._require_status(
            RunStatus.RUNNING,
            RunStatus.WAITING_FOR_WORKFLOW,
            target=RunStatus.SUCCEEDED,
        )
        self._ended_at = self._validated_end(at)
        self._status = RunStatus.SUCCEEDED

    def fail(self, at: datetime, failure: Failure) -> None:
        self._require_status(
            RunStatus.RUNNING,
            RunStatus.WAITING_FOR_WORKFLOW,
            target=RunStatus.FAILED,
        )
        self._ended_at = self._validated_end(at)
        self._failure = failure
        self._status = RunStatus.FAILED

    def cancel(self, at: datetime) -> None:
        self._require_status(
            RunStatus.QUEUED,
            RunStatus.RUNNING,
            RunStatus.WAITING_FOR_APPROVAL,
            RunStatus.WAITING_FOR_DELEGATION,
            RunStatus.WAITING_FOR_WORKFLOW,
            target=RunStatus.CANCELLED,
        )
        self._ended_at = self._validated_end(at)
        self._approval_request_id = None
        self._delegation_request_id = None
        self._status = RunStatus.CANCELLED

    def succeed_workflow(self, at: datetime) -> None:
        self._require_status(RunStatus.WAITING_FOR_WORKFLOW, target=RunStatus.SUCCEEDED)
        self.succeed(at)

    def fail_workflow(self, at: datetime, failure: Failure) -> None:
        self._require_status(RunStatus.WAITING_FOR_WORKFLOW, target=RunStatus.FAILED)
        self.fail(at, failure)

    def cancel_workflow(self, at: datetime) -> None:
        self._require_status(RunStatus.WAITING_FOR_WORKFLOW, target=RunStatus.CANCELLED)
        self.cancel(at)

    def _validated_end(self, at: datetime) -> datetime:
        end = ensure_utc(at)
        reference = self._started_at or self._created_at
        if end < reference:
            raise DomainValidationError("Run end timestamp precedes its start/creation time")
        return end
