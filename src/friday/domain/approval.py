"""ApprovalRequest: a pending or resolved request for human authorization.

Preserves the exact action requiring approval and never contains executable
behavior. Approving here does not resume a Run — coordinating an approval's
resolution with the waiting Run/RunStep is an application-service concern
(see docs/architecture/domain-model.md, section on Task/Run coordination).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.identifiers import ApprovalRequestId, RunId, RunStepId
from friday.domain.json_value import JsonValue, ensure_json_value
from friday.domain.time import ensure_utc


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


TERMINAL_APPROVAL_STATUSES = frozenset(
    {
        ApprovalStatus.APPROVED,
        ApprovalStatus.REJECTED,
        ApprovalStatus.CANCELLED,
        ApprovalStatus.EXPIRED,
    }
)


class ApprovalCategory(StrEnum):
    TOOL_EXECUTION = "tool_execution"
    FILESYSTEM_WRITE = "filesystem_write"
    NETWORK_ACCESS = "network_access"
    COMPUTER_USE = "computer_use"
    OTHER = "other"


@dataclass(slots=True)
class ApprovalRequest:
    _id: ApprovalRequestId
    _run_id: RunId
    _category: ApprovalCategory
    _summary: str
    _reason: str
    _requested_action: str
    _requested_input: JsonValue
    _status: ApprovalStatus
    _requested_at: datetime
    _step_id: RunStepId | None = field(default=None)
    _expires_at: datetime | None = field(default=None)
    _resolved_at: datetime | None = field(default=None)
    _resolution_note: str | None = field(default=None)
    _resolver: str | None = field(default=None)

    @classmethod
    def new(
        cls,
        *,
        id: ApprovalRequestId,
        run_id: RunId,
        category: ApprovalCategory,
        summary: str,
        reason: str,
        requested_action: str,
        requested_input: JsonValue,
        requested_at: datetime,
        step_id: RunStepId | None = None,
        expires_at: datetime | None = None,
    ) -> ApprovalRequest:
        normalized_summary = summary.strip()
        normalized_action = requested_action.strip()
        if not normalized_summary:
            raise DomainValidationError("ApprovalRequest.summary must not be empty")
        if not normalized_action:
            raise DomainValidationError("ApprovalRequest.requested_action must not be empty")
        normalized_expiry = ensure_utc(expires_at) if expires_at is not None else None
        if normalized_expiry is not None and normalized_expiry <= ensure_utc(requested_at):
            raise DomainValidationError("ApprovalRequest.expires_at must be after its requested_at")
        return cls(
            _id=id,
            _run_id=run_id,
            _category=category,
            _summary=normalized_summary,
            _reason=reason.strip(),
            _requested_action=normalized_action,
            _requested_input=ensure_json_value(
                requested_input, path="ApprovalRequest.requested_input"
            ),
            _status=ApprovalStatus.PENDING,
            _requested_at=ensure_utc(requested_at),
            _step_id=step_id,
            _expires_at=normalized_expiry,
        )

    @property
    def id(self) -> ApprovalRequestId:
        return self._id

    @property
    def run_id(self) -> RunId:
        return self._run_id

    @property
    def step_id(self) -> RunStepId | None:
        return self._step_id

    @property
    def category(self) -> ApprovalCategory:
        return self._category

    @property
    def summary(self) -> str:
        return self._summary

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def requested_action(self) -> str:
        return self._requested_action

    @property
    def requested_input(self) -> JsonValue:
        return self._requested_input

    @property
    def status(self) -> ApprovalStatus:
        return self._status

    @property
    def requested_at(self) -> datetime:
        return self._requested_at

    @property
    def expires_at(self) -> datetime | None:
        return self._expires_at

    @property
    def resolved_at(self) -> datetime | None:
        return self._resolved_at

    @property
    def resolution_note(self) -> str | None:
        return self._resolution_note

    @property
    def resolver(self) -> str | None:
        return self._resolver

    def _require_status(self, *allowed: ApprovalStatus, target: ApprovalStatus) -> None:
        if self._status not in allowed:
            raise InvalidStateTransition("ApprovalRequest", self._status.value, target.value)

    def _resolve(
        self,
        target: ApprovalStatus,
        at: datetime,
        resolver: str | None,
        resolution_note: str | None,
    ) -> None:
        self._require_status(ApprovalStatus.PENDING, target=target)
        self._resolved_at = ensure_utc(at)
        self._resolver = resolver
        self._resolution_note = resolution_note
        self._status = target

    def approve(self, at: datetime, resolver: str, resolution_note: str | None = None) -> None:
        self._resolve(ApprovalStatus.APPROVED, at, resolver, resolution_note)

    def reject(self, at: datetime, resolver: str, resolution_note: str | None = None) -> None:
        self._resolve(ApprovalStatus.REJECTED, at, resolver, resolution_note)

    def cancel(self, at: datetime, resolution_note: str | None = None) -> None:
        self._resolve(ApprovalStatus.CANCELLED, at, resolver=None, resolution_note=resolution_note)

    def expire(self, at: datetime) -> None:
        self._resolve(ApprovalStatus.EXPIRED, at, resolver=None, resolution_note=None)
