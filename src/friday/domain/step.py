"""RunStep: an ordered logical unit of execution within a Run.

Ordered steps only — no DAG/dependency/parallelism modeling here. That
belongs to later execution-planning work, once there's an actual scheduler
to plan for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.failure import Failure
from friday.domain.identifiers import ApprovalRequestId, RunId, RunStepId
from friday.domain.time import ensure_utc


class RunStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


TERMINAL_RUN_STEP_STATUSES = frozenset(
    {
        RunStepStatus.SUCCEEDED,
        RunStepStatus.FAILED,
        RunStepStatus.SKIPPED,
        RunStepStatus.CANCELLED,
    }
)


@dataclass(slots=True)
class RunStep:
    _id: RunStepId
    _run_id: RunId
    _name: str
    _position: int
    _status: RunStepStatus
    _created_at: datetime
    _started_at: datetime | None = field(default=None)
    _ended_at: datetime | None = field(default=None)
    _failure: Failure | None = field(default=None)
    _approval_request_id: ApprovalRequestId | None = field(default=None)

    @classmethod
    def new(
        cls,
        *,
        id: RunStepId,
        run_id: RunId,
        name: str,
        position: int,
        created_at: datetime,
    ) -> RunStep:
        normalized_name = name.strip()
        if not normalized_name:
            raise DomainValidationError("RunStep.name must not be empty after trimming")
        if position < 0:
            raise DomainValidationError("RunStep.position must be non-negative")
        return cls(
            _id=id,
            _run_id=run_id,
            _name=normalized_name,
            _position=position,
            _status=RunStepStatus.PENDING,
            _created_at=ensure_utc(created_at),
        )

    @property
    def id(self) -> RunStepId:
        return self._id

    @property
    def run_id(self) -> RunId:
        return self._run_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def position(self) -> int:
        return self._position

    @property
    def status(self) -> RunStepStatus:
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

    def _require_status(self, *allowed: RunStepStatus, target: RunStepStatus) -> None:
        if self._status not in allowed:
            raise InvalidStateTransition("RunStep", self._status.value, target.value)

    def start(self, at: datetime) -> None:
        self._require_status(RunStepStatus.PENDING, target=RunStepStatus.RUNNING)
        self._started_at = ensure_utc(at)
        self._status = RunStepStatus.RUNNING

    def wait_for_approval(self, at: datetime, approval_request_id: ApprovalRequestId) -> None:
        self._require_status(RunStepStatus.RUNNING, target=RunStepStatus.WAITING_FOR_APPROVAL)
        ensure_utc(at)
        self._approval_request_id = approval_request_id
        self._status = RunStepStatus.WAITING_FOR_APPROVAL

    def resume(self, at: datetime) -> None:
        self._require_status(RunStepStatus.WAITING_FOR_APPROVAL, target=RunStepStatus.RUNNING)
        ensure_utc(at)
        self._approval_request_id = None
        self._status = RunStepStatus.RUNNING

    def succeed(self, at: datetime) -> None:
        self._require_status(RunStepStatus.RUNNING, target=RunStepStatus.SUCCEEDED)
        self._ended_at = self._validated_end(at)
        self._status = RunStepStatus.SUCCEEDED

    def fail(self, at: datetime, failure: Failure) -> None:
        self._require_status(RunStepStatus.RUNNING, target=RunStepStatus.FAILED)
        self._ended_at = self._validated_end(at)
        self._failure = failure
        self._status = RunStepStatus.FAILED

    def skip(self, at: datetime) -> None:
        self._require_status(RunStepStatus.PENDING, target=RunStepStatus.SKIPPED)
        self._ended_at = self._validated_end(at)
        self._status = RunStepStatus.SKIPPED

    def cancel(self, at: datetime) -> None:
        self._require_status(
            RunStepStatus.PENDING,
            RunStepStatus.RUNNING,
            RunStepStatus.WAITING_FOR_APPROVAL,
            target=RunStepStatus.CANCELLED,
        )
        self._ended_at = self._validated_end(at)
        self._approval_request_id = None
        self._status = RunStepStatus.CANCELLED

    def _validated_end(self, at: datetime) -> datetime:
        end = ensure_utc(at)
        reference = self._started_at or self._created_at
        if end < reference:
            raise DomainValidationError("RunStep end timestamp precedes its start/creation time")
        return end
