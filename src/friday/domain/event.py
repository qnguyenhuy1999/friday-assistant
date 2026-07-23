"""RunEvent: an append-only fact emitted during execution, ordered per Run.

Not an event-sourced aggregate — RunEvent is an audit/execution stream, not
the sole source of truth for reconstructing Task/Run/RunStep state in this
phase. Event creation itself is an application-layer concern (allocating
sequence numbers via RunEventStore); see docs/architecture/domain-model.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import RunEventId, RunId, RunStepId
from friday.domain.json_value import JsonValue, ensure_json_value
from friday.domain.time import ensure_utc


class RunEventType(StrEnum):
    RUN_CREATED = "run_created"
    RUN_STARTED = "run_started"
    RUN_WAITING_FOR_APPROVAL = "run_waiting_for_approval"
    RUN_RESUMED = "run_resumed"
    RUN_SUCCEEDED = "run_succeeded"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"
    STEP_CREATED = "step_created"
    STEP_STARTED = "step_started"
    STEP_SUCCEEDED = "step_succeeded"
    STEP_FAILED = "step_failed"
    STEP_SKIPPED = "step_skipped"
    STEP_CANCELLED = "step_cancelled"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    TOOL_INVOCATION_REQUESTED = "tool_invocation_requested"
    TOOL_INVOCATION_STARTED = "tool_invocation_started"
    TOOL_INVOCATION_SUCCEEDED = "tool_invocation_succeeded"
    TOOL_INVOCATION_FAILED = "tool_invocation_failed"
    TOOL_INVOCATION_CANCELLED = "tool_invocation_cancelled"
    ARTIFACT_CREATED = "artifact_created"


@dataclass(frozen=True, slots=True)
class RunEvent:
    id: RunEventId
    run_id: RunId
    type: RunEventType
    sequence: int
    occurred_at: datetime
    payload: JsonValue = field(default=None)
    step_id: RunStepId | None = field(default=None)

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise DomainValidationError("RunEvent.sequence must be a positive integer")
        object.__setattr__(self, "occurred_at", ensure_utc(self.occurred_at))
        object.__setattr__(
            self, "payload", ensure_json_value(self.payload, path="RunEvent.payload")
        )
