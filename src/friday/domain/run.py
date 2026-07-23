"""Run: one execution attempt for a Task. Owns execution lifecycle state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.failure import Failure
from friday.domain.identifiers import ApprovalRequestId, RunId, TaskId
from friday.domain.time import ensure_utc


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_RUN_STATUSES = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED})


@dataclass(slots=True)
class Run:
    _id: RunId
    _task_id: TaskId
    _status: RunStatus
    _created_at: datetime
    _started_at: datetime | None = field(default=None)
    _ended_at: datetime | None = field(default=None)
    _failure: Failure | None = field(default=None)
    _approval_request_id: ApprovalRequestId | None = field(default=None)

    @classmethod
    def new(cls, *, id: RunId, task_id: TaskId, created_at: datetime) -> Run:
        return cls(
            _id=id,
            _task_id=task_id,
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

    def _require_status(self, *allowed: RunStatus, target: RunStatus) -> None:
        if self._status not in allowed:
            raise InvalidStateTransition("Run", self._status.value, target.value)

    def start(self, at: datetime) -> None:
        self._require_status(RunStatus.QUEUED, target=RunStatus.RUNNING)
        self._started_at = ensure_utc(at)
        self._status = RunStatus.RUNNING

    def wait_for_approval(self, at: datetime, approval_request_id: ApprovalRequestId) -> None:
        self._require_status(RunStatus.RUNNING, target=RunStatus.WAITING_FOR_APPROVAL)
        ensure_utc(at)
        self._approval_request_id = approval_request_id
        self._status = RunStatus.WAITING_FOR_APPROVAL

    def resume(self, at: datetime) -> None:
        self._require_status(RunStatus.WAITING_FOR_APPROVAL, target=RunStatus.RUNNING)
        ensure_utc(at)
        self._approval_request_id = None
        self._status = RunStatus.RUNNING

    def succeed(self, at: datetime) -> None:
        self._require_status(RunStatus.RUNNING, target=RunStatus.SUCCEEDED)
        self._ended_at = self._validated_end(at)
        self._status = RunStatus.SUCCEEDED

    def fail(self, at: datetime, failure: Failure) -> None:
        self._require_status(RunStatus.RUNNING, target=RunStatus.FAILED)
        self._ended_at = self._validated_end(at)
        self._failure = failure
        self._status = RunStatus.FAILED

    def cancel(self, at: datetime) -> None:
        self._require_status(
            RunStatus.QUEUED,
            RunStatus.RUNNING,
            RunStatus.WAITING_FOR_APPROVAL,
            target=RunStatus.CANCELLED,
        )
        self._ended_at = self._validated_end(at)
        self._approval_request_id = None
        self._status = RunStatus.CANCELLED

    def _validated_end(self, at: datetime) -> datetime:
        end = ensure_utc(at)
        reference = self._started_at or self._created_at
        if end < reference:
            raise DomainValidationError("Run end timestamp precedes its start/creation time")
        return end
