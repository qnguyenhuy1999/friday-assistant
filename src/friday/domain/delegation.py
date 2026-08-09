"""Delegation contract: durable provenance edge from a parent Run to a future
child Task/Run executed by a target Agent.  Step 1 is persistence only — no
child Run is dispatched from a DelegationRequest here.  The target Agent
grants no authority: a future child Run must still execute through Friday's
normal AgentRunProcessor -> ToolGateway -> approval path.  A parent Agent
cannot approve child actions, a parent approval cannot authorize child
actions, and this fingerprint is never a tool authorization fingerprint —
see friday.application.tool_authorization for that unrelated fingerprint."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.identifiers import (
    AgentId,
    DelegationRequestId,
    RunId,
    RunStepId,
    TaskId,
)
from friday.domain.json_value import JsonValue, ensure_json_value
from friday.domain.time import ensure_utc

MAX_OBJECTIVE_LENGTH = 4_000
MAX_OUTPUT_CONTRACT_LENGTH = 4_000
MAX_FAILURE_CODE_LENGTH = 128
FINGERPRINT_VERSION = 1


class DelegationStatus(StrEnum):
    REQUESTED = "requested"
    DISPATCHED = "dispatched"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_DELEGATION_STATUSES = frozenset(
    {DelegationStatus.SUCCEEDED, DelegationStatus.FAILED, DelegationStatus.CANCELLED}
)


def compute_delegation_fingerprint(
    *,
    delegation_request_id: DelegationRequestId,
    parent_run_id: RunId,
    parent_run_step_id: RunStepId | None,
    target_agent_id: AgentId,
    objective: str,
    input_payload: JsonValue,
    expected_output_contract: str,
) -> str:
    """Deterministic SHA-256 binding one exact delegation intent to one
    parent Run. Canonical JSON (sorted keys, no whitespace) makes the
    fingerprint independent of input key order but sensitive to any value
    change in objective, target, input, or output contract."""
    payload = {
        "version": FINGERPRINT_VERSION,
        "delegation_request_id": str(delegation_request_id),
        "parent_run_id": str(parent_run_id),
        "parent_run_step_id": str(parent_run_step_id) if parent_run_step_id else None,
        "target_agent_id": str(target_agent_id),
        "objective": objective,
        "input_payload": input_payload,
        "expected_output_contract": expected_output_contract,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(slots=True)
class DelegationRequest:
    id: DelegationRequestId
    parent_run_id: RunId
    target_agent_id: AgentId
    objective: str
    input_payload: JsonValue
    expected_output_contract: str
    authorization_fingerprint: str
    status: DelegationStatus
    created_at: datetime
    parent_run_step_id: RunStepId | None = None
    child_task_id: TaskId | None = None
    child_run_id: RunId | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        objective = self.objective.strip()
        if not objective or len(objective) > MAX_OBJECTIVE_LENGTH:
            raise DomainValidationError(
                "DelegationRequest.objective must be non-empty and within the maximum length"
            )
        object.__setattr__(self, "objective", objective)
        contract = self.expected_output_contract.strip()
        if not contract or len(contract) > MAX_OUTPUT_CONTRACT_LENGTH:
            raise DomainValidationError(
                "DelegationRequest.expected_output_contract must be non-empty and within the "
                "maximum length"
            )
        object.__setattr__(self, "expected_output_contract", contract)
        ensure_json_value(self.input_payload, path="DelegationRequest.input_payload")
        if len(self.authorization_fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in self.authorization_fingerprint
        ):
            raise DomainValidationError(
                "DelegationRequest.authorization_fingerprint must be lowercase sha256"
            )
        expected_fingerprint = compute_delegation_fingerprint(
            delegation_request_id=self.id,
            parent_run_id=self.parent_run_id,
            parent_run_step_id=self.parent_run_step_id,
            target_agent_id=self.target_agent_id,
            objective=self.objective,
            input_payload=self.input_payload,
            expected_output_contract=self.expected_output_contract,
        )
        if self.authorization_fingerprint != expected_fingerprint:
            raise DomainValidationError("delegation_fingerprint_mismatch")
        if self.failure_code is not None and len(self.failure_code) > MAX_FAILURE_CODE_LENGTH:
            raise DomainValidationError("DelegationRequest.failure_code exceeds the maximum length")
        if self.status is DelegationStatus.FAILED and self.failure_code is None:
            raise DomainValidationError("failed DelegationRequest requires a failure_code")
        if self.status is not DelegationStatus.FAILED and self.failure_code is not None:
            raise DomainValidationError("only a failed DelegationRequest may carry a failure_code")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        if self.started_at is not None:
            object.__setattr__(self, "started_at", ensure_utc(self.started_at))
        if self.completed_at is not None:
            object.__setattr__(self, "completed_at", ensure_utc(self.completed_at))
        self._validate_state_shape()

    def _validate_state_shape(self) -> None:
        dispatched = self.child_task_id is not None and self.child_run_id is not None
        if (self.child_task_id is None) != (self.child_run_id is None):
            raise DomainValidationError("delegation child task/run ids must be paired")
        if self.status is DelegationStatus.REQUESTED:
            if dispatched or self.started_at is not None or self.completed_at is not None:
                raise DomainValidationError(
                    "requested delegation cannot have child execution state"
                )
            if self.failure_code is not None:
                raise DomainValidationError("requested delegation cannot have a failure code")
        elif self.status is DelegationStatus.DISPATCHED:
            if not dispatched or self.started_at is None or self.completed_at is not None:
                raise DomainValidationError("dispatched delegation has invalid execution state")
            if self.failure_code is not None:
                raise DomainValidationError("dispatched delegation cannot have a failure code")
        elif self.status is DelegationStatus.SUCCEEDED:
            if not dispatched or self.started_at is None or self.completed_at is None:
                raise DomainValidationError("succeeded delegation has invalid execution state")
            if self.failure_code is not None:
                raise DomainValidationError("succeeded delegation cannot have a failure code")
        elif self.status is DelegationStatus.FAILED:
            if not dispatched or self.started_at is None or self.completed_at is None:
                raise DomainValidationError("failed delegation has invalid execution state")
            if not self.failure_code:
                raise DomainValidationError("failed DelegationRequest requires a failure_code")
        elif self.status is DelegationStatus.CANCELLED:
            if self.failure_code is not None:
                raise DomainValidationError("cancelled delegation cannot have a failure code")
            if dispatched and (self.started_at is None or self.completed_at is None):
                raise DomainValidationError(
                    "dispatched cancelled delegation has invalid execution state"
                )

    def dispatch(self, child_task_id: TaskId, child_run_id: RunId, at: datetime) -> None:
        if self.status is not DelegationStatus.REQUESTED:
            raise InvalidStateTransition("DelegationRequest", self.status.value, "dispatched")
        if child_task_id is None or child_run_id is None:
            raise DomainValidationError("dispatch requires child task and run ids")
        self.child_task_id = child_task_id
        self.child_run_id = child_run_id
        self.started_at = ensure_utc(at)
        self.status = DelegationStatus.DISPATCHED

    def succeed(self, at: datetime) -> None:
        if self.status is not DelegationStatus.DISPATCHED:
            raise InvalidStateTransition("DelegationRequest", self.status.value, "succeeded")
        self.completed_at = ensure_utc(at)
        self.status = DelegationStatus.SUCCEEDED

    def fail(self, at: datetime, failure_code: str) -> None:
        if self.status is not DelegationStatus.DISPATCHED:
            raise InvalidStateTransition("DelegationRequest", self.status.value, "failed")
        code = failure_code.strip()
        if not code or len(code) > MAX_FAILURE_CODE_LENGTH:
            raise DomainValidationError("delegation failure code is empty or too long")
        self.failure_code = code
        self.completed_at = ensure_utc(at)
        self.status = DelegationStatus.FAILED

    def cancel(self, at: datetime) -> None:
        if self.status not in {DelegationStatus.REQUESTED, DelegationStatus.DISPATCHED}:
            raise InvalidStateTransition("DelegationRequest", self.status.value, "cancelled")
        self.completed_at = ensure_utc(at)
        self.status = DelegationStatus.CANCELLED

    @classmethod
    def new(
        cls,
        *,
        id: DelegationRequestId,
        parent_run_id: RunId,
        target_agent_id: AgentId,
        objective: str,
        input_payload: JsonValue,
        expected_output_contract: str,
        created_at: datetime,
        parent_run_step_id: RunStepId | None = None,
    ) -> DelegationRequest:
        normalized_objective = objective.strip()
        normalized_contract = expected_output_contract.strip()
        canonical_input = ensure_json_value(input_payload, path="DelegationRequest.input_payload")
        fingerprint = compute_delegation_fingerprint(
            delegation_request_id=id,
            parent_run_id=parent_run_id,
            parent_run_step_id=parent_run_step_id,
            target_agent_id=target_agent_id,
            objective=normalized_objective,
            input_payload=canonical_input,
            expected_output_contract=normalized_contract,
        )
        return cls(
            id=id,
            parent_run_id=parent_run_id,
            target_agent_id=target_agent_id,
            objective=normalized_objective,
            input_payload=canonical_input,
            expected_output_contract=normalized_contract,
            authorization_fingerprint=fingerprint,
            status=DelegationStatus.REQUESTED,
            created_at=ensure_utc(created_at),
            parent_run_step_id=parent_run_step_id,
        )
