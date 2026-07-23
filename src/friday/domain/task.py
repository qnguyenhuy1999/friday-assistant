"""Task: a user-level requested outcome. Not the execution process itself —
see Run for that. A Task may have multiple Runs over time (coordination
between the two is an application-layer concern, not encoded here)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.failure import Failure
from friday.domain.identifiers import TaskId
from friday.domain.time import ensure_utc


class TaskStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_TASK_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED})


@dataclass(slots=True)
class Task:
    _id: TaskId
    _title: str
    _description: str
    _status: TaskStatus
    _created_at: datetime
    _started_at: datetime | None = field(default=None)
    _completed_at: datetime | None = field(default=None)
    _failed_at: datetime | None = field(default=None)
    _cancelled_at: datetime | None = field(default=None)
    _failure: Failure | None = field(default=None)

    @classmethod
    def new(cls, *, id: TaskId, title: str, description: str, created_at: datetime) -> Task:
        normalized_title = title.strip()
        if not normalized_title:
            raise DomainValidationError("Task.title must not be empty after trimming")
        return cls(
            _id=id,
            _title=normalized_title,
            _description=description.strip(),
            _status=TaskStatus.PENDING,
            _created_at=ensure_utc(created_at),
        )

    @property
    def id(self) -> TaskId:
        return self._id

    @property
    def title(self) -> str:
        return self._title

    @property
    def description(self) -> str:
        return self._description

    @property
    def status(self) -> TaskStatus:
        return self._status

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def started_at(self) -> datetime | None:
        return self._started_at

    @property
    def completed_at(self) -> datetime | None:
        return self._completed_at

    @property
    def failed_at(self) -> datetime | None:
        return self._failed_at

    @property
    def cancelled_at(self) -> datetime | None:
        return self._cancelled_at

    @property
    def failure(self) -> Failure | None:
        return self._failure

    def _require_status(self, *allowed: TaskStatus, target: TaskStatus) -> None:
        if self._status not in allowed:
            raise InvalidStateTransition("Task", self._status.value, target.value)

    def start(self, at: datetime) -> None:
        self._require_status(TaskStatus.PENDING, target=TaskStatus.ACTIVE)
        self._started_at = ensure_utc(at)
        self._status = TaskStatus.ACTIVE

    def complete(self, at: datetime) -> None:
        self._require_status(TaskStatus.ACTIVE, target=TaskStatus.COMPLETED)
        self._completed_at = ensure_utc(at)
        self._status = TaskStatus.COMPLETED

    def fail(self, at: datetime, failure: Failure) -> None:
        self._require_status(TaskStatus.ACTIVE, target=TaskStatus.FAILED)
        self._failed_at = ensure_utc(at)
        self._failure = failure
        self._status = TaskStatus.FAILED

    def cancel(self, at: datetime) -> None:
        self._require_status(TaskStatus.PENDING, TaskStatus.ACTIVE, target=TaskStatus.CANCELLED)
        self._cancelled_at = ensure_utc(at)
        self._status = TaskStatus.CANCELLED
