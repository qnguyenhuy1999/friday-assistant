"""Append-only, task-owned lifecycle facts for transitions without a Run."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import TaskEventId, TaskId
from friday.domain.json_value import JsonValue, ensure_json_value
from friday.domain.time import ensure_utc


class TaskEventType(StrEnum):
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"


@dataclass(frozen=True, slots=True)
class TaskEvent:
    id: TaskEventId
    task_id: TaskId
    type: TaskEventType
    sequence: int
    occurred_at: datetime
    payload: JsonValue = field(default=None)

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise DomainValidationError("TaskEvent.sequence must be a positive integer")
        object.__setattr__(self, "occurred_at", ensure_utc(self.occurred_at))
        object.__setattr__(
            self, "payload", ensure_json_value(self.payload, path="TaskEvent.payload")
        )
