"""ToolInvocation: an attempt to invoke a tool or external capability.

Records requested input, lifecycle state, and outcome metadata only — no
tool registry, execution, permission checks, or subprocess handling belongs
here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.failure import Failure
from friday.domain.identifiers import ApprovalRequestId, RunId, RunStepId, ToolInvocationId
from friday.domain.json_value import JsonValue, ensure_json_value
from friday.domain.time import ensure_utc

_UNSET = object()


class ToolInvocationStatus(StrEnum):
    REQUESTED = "requested"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_TOOL_INVOCATION_STATUSES = frozenset(
    {
        ToolInvocationStatus.SUCCEEDED,
        ToolInvocationStatus.FAILED,
        ToolInvocationStatus.CANCELLED,
    }
)


@dataclass(slots=True)
class ToolInvocation:
    _id: ToolInvocationId
    _run_id: RunId
    _tool_name: str
    _requested_input: JsonValue
    _status: ToolInvocationStatus
    _requested_at: datetime
    _step_id: RunStepId | None = field(default=None)
    _approval_request_id: ApprovalRequestId | None = field(default=None)
    _started_at: datetime | None = field(default=None)
    _completed_at: datetime | None = field(default=None)
    _output: JsonValue = field(default=None)
    _output_set: bool = field(default=False)
    _failure: Failure | None = field(default=None)

    @classmethod
    def new(
        cls,
        *,
        id: ToolInvocationId,
        run_id: RunId,
        tool_name: str,
        requested_input: JsonValue,
        requested_at: datetime,
        step_id: RunStepId | None = None,
        approval_request_id: ApprovalRequestId | None = None,
    ) -> ToolInvocation:
        normalized_name = tool_name.strip()
        if not normalized_name:
            raise DomainValidationError("ToolInvocation.tool_name must not be empty")
        return cls(
            _id=id,
            _run_id=run_id,
            _tool_name=normalized_name,
            _requested_input=ensure_json_value(
                requested_input, path="ToolInvocation.requested_input"
            ),
            _status=ToolInvocationStatus.REQUESTED,
            _requested_at=ensure_utc(requested_at),
            _step_id=step_id,
            _approval_request_id=approval_request_id,
        )

    @property
    def id(self) -> ToolInvocationId:
        return self._id

    @property
    def run_id(self) -> RunId:
        return self._run_id

    @property
    def step_id(self) -> RunStepId | None:
        return self._step_id

    @property
    def tool_name(self) -> str:
        return self._tool_name

    @property
    def requested_input(self) -> JsonValue:
        return self._requested_input

    @property
    def status(self) -> ToolInvocationStatus:
        return self._status

    @property
    def requested_at(self) -> datetime:
        return self._requested_at

    @property
    def started_at(self) -> datetime | None:
        return self._started_at

    @property
    def completed_at(self) -> datetime | None:
        return self._completed_at

    @property
    def output(self) -> JsonValue:
        return self._output

    @property
    def output_set(self) -> bool:
        return self._output_set

    @property
    def failure(self) -> Failure | None:
        return self._failure

    @property
    def approval_request_id(self) -> ApprovalRequestId | None:
        return self._approval_request_id

    def _require_status(self, *allowed: ToolInvocationStatus, target: ToolInvocationStatus) -> None:
        if self._status not in allowed:
            raise InvalidStateTransition("ToolInvocation", self._status.value, target.value)

    def start(self, at: datetime) -> None:
        self._require_status(ToolInvocationStatus.REQUESTED, target=ToolInvocationStatus.RUNNING)
        self._started_at = ensure_utc(at)
        self._status = ToolInvocationStatus.RUNNING

    def succeed(self, at: datetime, output: JsonValue = _UNSET) -> None:  # type: ignore[assignment]
        self._require_status(ToolInvocationStatus.RUNNING, target=ToolInvocationStatus.SUCCEEDED)
        if output is _UNSET:
            raise DomainValidationError(
                "ToolInvocation.succeed requires an explicit output (None is a valid output)"
            )
        self._completed_at = self._validated_end(at)
        self._output = ensure_json_value(output, path="ToolInvocation.output")
        self._output_set = True
        self._status = ToolInvocationStatus.SUCCEEDED

    def fail(self, at: datetime, failure: Failure) -> None:
        self._require_status(ToolInvocationStatus.RUNNING, target=ToolInvocationStatus.FAILED)
        self._completed_at = self._validated_end(at)
        self._failure = failure
        self._status = ToolInvocationStatus.FAILED

    def cancel(self, at: datetime) -> None:
        self._require_status(
            ToolInvocationStatus.REQUESTED,
            ToolInvocationStatus.RUNNING,
            target=ToolInvocationStatus.CANCELLED,
        )
        self._completed_at = self._validated_end(at)
        self._status = ToolInvocationStatus.CANCELLED

    def _validated_end(self, at: datetime) -> datetime:
        end = ensure_utc(at)
        reference = self._started_at or self._requested_at
        if end < reference:
            raise DomainValidationError(
                "ToolInvocation end timestamp precedes its start/request time"
            )
        return end
